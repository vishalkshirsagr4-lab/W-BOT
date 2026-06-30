const dotenv = require('dotenv');
dotenv.config();

const axios = require('axios');
const express = require('express');
const dns = require('dns');
const NodeCache = require('node-cache');

const pino = require('pino');
const {
  makeWASocket,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  DisconnectReason,
  makeCacheableSignalKeyStore,
  Browsers,
} = require('@whiskeysockets/baileys');

dns.setServers(['8.8.8.8', '8.8.4.4']);

// ---------- Dummy Server for Render ----------
const app = express();
app.get('/', (req, res) => res.send('Nezuko Bot is Awake! 🌸'));
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`[WA][INFO] Dummy server listening on port ${PORT}`));

// ---------- Config ----------
const FASTAPI_URL = process.env.FASTAPI_URL || 'http://localhost:8000';
const OWNER_NUMBER = process.env.OWNER_NUMBER || '';
const BOT_NAME = process.env.BOT_NAME || 'College Community Bot';
const BOT_PREFIX = process.env.BOT_PREFIX || '/';
const MAX_MESSAGE_LENGTH = Number(process.env.MAX_MESSAGE_LENGTH || '4000');

// Baileys auth state directory (multi-file)
const AUTH_DIR = process.env.WA_AUTH_DIR || './.baileys_auth';

// Rate limiting
const RATE_LIMIT_WINDOW_MS = 10_000;
const RATE_LIMIT_MAX = 6; // messages per window
const senderBuckets = new Map();

// Cache to prevent duplicate message processing (TTL: 120 seconds)
const messageCache = new NodeCache({ stdTTL: 120, checkperiod: 15 });

// Cache for repeated questions -> avoid repeated Gemini/AI calls
const aiResponseCache = new NodeCache({ stdTTL: 10 * 60, checkperiod: 60 }); // 10 minutes

// Prevent parallel AI work per sender (keeps latency stable)
const inFlightBySender = new Map();


const WHATSAPP_API_ENDPOINT = `${FASTAPI_URL}/api/v1/whatsapp/message`;

// ---------- Logging helpers ----------
function logInfo(msg, obj) {
  if (obj !== undefined) console.log(`[WA][INFO] ${msg}`, obj);
  else console.log(`[WA][INFO] ${msg}`);
}
function logWarn(msg, obj) {
  if (obj !== undefined) console.warn(`[WA][WARN] ${msg}`, obj);
  else console.warn(`[WA][WARN] ${msg}`);
}
function logError(msg, obj) {
  if (obj !== undefined) console.error(`[WA][ERROR] ${msg}`, obj);
  else console.error(`[WA][ERROR] ${msg}`);
}

function isRateLimited(senderId) {
  const now = Date.now();
  const bucket = senderBuckets.get(senderId) || { start: now, count: 0 };

  if (now - bucket.start > RATE_LIMIT_WINDOW_MS) {
    bucket.start = now;
    bucket.count = 0;
  }

  bucket.count += 1;
  senderBuckets.set(senderId, bucket);

  return bucket.count > RATE_LIMIT_MAX;
}

function isOwner(senderId) {
  if (!OWNER_NUMBER) return false;
  return senderId === `${OWNER_NUMBER}@s.whatsapp.net` || senderId === OWNER_NUMBER;
}

async function handleOwnerCommand(sock, msg, text) {
  if (!isOwner(msg.key?.remoteJid)) return false;

  const normalized = String(text || '').trim();
  const cmd = normalized.split(/\s+/)[0];

  if (cmd === '/restart' || cmd === `${BOT_PREFIX}restart`) {
    await sock.sendMessage(msg.key.remoteJid, { text: 'Restarting session... 🔄' }, { quoted: msg });
    setTimeout(() => process.exit(0), 1000);
    return true;
  }

  if (cmd === '/status' || cmd === `${BOT_PREFIX}status`) {
    await sock.sendMessage(msg.key.remoteJid, { text: `Status ✅\nBot: ${BOT_NAME}` }, { quoted: msg });
    return true;
  }

  if (cmd === '/help' || cmd === `${BOT_PREFIX}help`) {
    await sock.sendMessage(
      msg.key.remoteJid,
      {
        text: [
          `${BOT_NAME} owner commands:`,
          `- /status`,
          `- /restart`,
          `Other commands are handled via FastAPI access control.`,
        ].join('\n'),
      },
      { quoted: msg }
    );
    return true;
  }

  return false;
}

function safeSlice(s, n) {
  const str = String(s ?? '');
  if (str.length <= n) return str;
  return str.slice(0, n);
}

function normalizeQuotedText(m) {
  // Keep compatibility with your FastAPI payload: quoted_text is a string body (best effort)
  if (!m.quotedMsg || !m.quotedMsg.message) return null;

  const q = m.quotedMsg.message;
  // Text-like fields differ by message type
  if (q.conversation) return q.conversation;
  if (q.extendedTextMessage?.text) return q.extendedTextMessage.text;
  return null;
}

async function normalizeWhatsAppMessage(sock, m) {
  const fromJid = m.key?.remoteJid;
  const isGroup = !!fromJid && fromJid.endsWith('@g.us');

  const authorJid = m.key?.participant || fromJid;
  const userId = authorJid;

  const phoneNumber = (authorJid || '')
    .replace('@c.us', '')
    .replace('@g.us', '')
    .replace('@s.whatsapp.net', '') || '';

  // sender name best-effort
  let senderName = 'Unknown';
  let profileName = '';
  try {
    const contact = await sock.profilePictureUrl(authorJid, 'contacts');
    // profilePictureUrl doesn't provide name; keep unknown
    void contact;
  } catch {
    // ignore
  }

  // quoted
  const quotedText = normalizeQuotedText(m);

  const body = safeSlice(m.messageText || m.message?.conversation || '', MAX_MESSAGE_LENGTH);

  // timestamp (seconds)
  const timestamp = m.messageTimestamp ? Number(m.messageTimestamp) : Math.floor(Date.now() / 1000);

  const messageType = m.message?.type || (m.message?.conversation ? 'text' : 'text');

  return {
    platform_id: userId,
    phone_number: phoneNumber,

    sender_name: senderName,
    profile_name: profileName,

    chat_id: fromJid,
    group_id: isGroup ? fromJid : null,
    group_name: isGroup ? fromJid : null,

    message: body,
    quoted_message: quotedText,

    // Keep same shape as your FastAPI Pydantic model expects
    media: m.message?.imageMessage ? { type: 'image' } : m.message?.videoMessage ? { type: 'video' } : null,
    location: null,
    sticker: null,
    voice: null,

    timestamp,
    message_type: messageType,

    is_group: isGroup,
    quoted_text: quotedText,
  };
}

async function forwardToFastAPI(payload, retries = 1) {
  // Hard timeout: keep WhatsApp replies within 2–5 seconds.
  const timeoutMs = Number(process.env.FASTAPI_TIMEOUT_MS || '8000');

  const cacheKey = payload?.platform_id ? `${payload.platform_id}|${payload.message}|${payload.is_group}` : null;

  if (cacheKey) {
    const cached = aiResponseCache.get(cacheKey);
    if (cached) return cached;
  }

  const fallbackReply = process.env.FASTAPI_FALLBACK_REPLY || 'Sorry! My brain is taking time. Try again in a bit 😭';

  // If there's already an AI call in-flight for this sender, do not stack requests.
  // This keeps WhatsApp response latency stable under load.
  // (We only use this when cache misses.)
  const senderKey = payload?.platform_id;
  if (senderKey) {
    const existing = inFlightBySender.get(senderKey);
    if (existing) return existing;
  }

  let inFlightPromise = (async () => {
    for (let attempt = 1; attempt <= retries; attempt++) {
      try {
        const controller = new AbortController();
        const t = setTimeout(() => controller.abort(), timeoutMs);

        const response = await axios.post(WHATSAPP_API_ENDPOINT, payload, {
          headers: { 'Content-Type': 'application/json' },
          signal: controller.signal,
        }).finally(() => clearTimeout(t));

        return response.data;
      } catch (error) {
        logWarn(`FastAPI request attempt ${attempt} failed: ${error?.message || error}`);

        if (attempt === retries) {
          return { __timeout: true, reply: fallbackReply };
        }

        await new Promise((res) => setTimeout(res, 1000 * Math.pow(2, attempt - 1)));
      }
    }
    return { __timeout: true, reply: fallbackReply };
  })();

  if (senderKey) inFlightBySender.set(senderKey, inFlightPromise);

  try {
    const result = await inFlightPromise;
    if (cacheKey) aiResponseCache.set(cacheKey, result);
    return result;
  } finally {
    if (senderKey) inFlightBySender.delete(senderKey);
  }
}


function extractMessageText(m) {
  const msg = m.message;
  if (!msg) return '';
  if (msg.conversation) return msg.conversation;
  if (msg.extendedTextMessage?.text) return msg.extendedTextMessage.text;
  if (msg.imageMessage?.caption) return msg.imageMessage.caption;
  if (msg.videoMessage?.caption) return msg.videoMessage.caption;
  return '';
}

async function start() {
  logInfo('Starting Baileys socket...');

  const logger = pino({ level: process.env.BAILEYS_LOG_LEVEL || 'info' });

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    logger,

    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    version,
    // group-friendly defaults
    // Browsers is safe; Render doesn't require any browser
    browser: Browsers.ubuntu('Chrome'),
    syncFullHistory: false,
    markOnlineOnConnect: true,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      try {
        // Cloud-friendly QR: data URL that you can open in a browser.
        // Render native logs often corrupt terminal ASCII QR.
        const qrcode = require('qrcode');
        qrcode
          .toDataURL(qr, { errorCorrectionLevel: 'M', margin: 1, scale: 6 })
          .then((dataUrl) => {
            logInfo('[WA][QR] Scan QR from this data URL (open in browser):', { dataUrl });
          })
          .catch((e) => {
            logError('[WA][QR] Failed to generate QR data URL', e?.message || e);
          });
      } catch (e) {
        logError('[WA][QR] QR handling error', e?.message || e);
      }
    }

    if (connection === 'close') {
      const shouldReconnect =
        lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
      logWarn('Connection closed', { shouldReconnect, lastDisconnect: lastDisconnect?.error?.message });
      if (shouldReconnect) {
        setTimeout(() => start().catch((e) => logError('Restart start() failed', e)), 1500);
      }
    }
  });

  sock.ev.on('messages.upsert', (event) => {
    // IMPORTANT: keep this handler non-blocking; do not await FastAPI inline.
    setImmediate(() => {
      handleUpsertEvent(sock, event).catch((e) => {
        logError('messages.upsert outer handler error', { message: e?.message, stack: e?.stack });
      });
    });
  });

  async function handleUpsertEvent(sock, event) {
    try {
      const m = event?.messages?.[0];

      if (!m || !m.key) return;

      // Dedup
      const msgId = m.key?.id;
      if (!msgId) return;
      if (messageCache.has(msgId)) return;
      messageCache.set(msgId, true);

      // Ignore messages from self
      if (m.key?.fromMe) return;

      const fromJid = m.key.remoteJid;
      if (!fromJid) return;

      // Best-effort: ignore broadcast/status
      if (fromJid.endsWith('@broadcast') || fromJid.endsWith('broadcast')) return;

      const body = extractMessageText(m);
      if (!body && !m.message?.imageMessage && !m.message?.videoMessage) return;

      // Trigger check: ONLY respond when user message contains "nezuko" (case-insensitive)
      const textForTrigger = String(body ?? '').toLowerCase();
      if (!textForTrigger.includes('nezuko')) {
        logInfo('[SKIP] No trigger', { sender: senderId });
        return;
      }
      logInfo('[TRIGGERED] Nezuko activated', { sender: senderId });


      const senderId = m.key.participant || fromJid;

      if (isRateLimited(senderId)) {
        logWarn('Rate limited sender:', senderId);
        return;
      }

      // Immediately acknowledge/typing (doesn't wait for AI)
      try {
        await sock.sendMessage(fromJid, { text: '⏳' });
      } catch {}

      const ownerHandled = await handleOwnerCommand(sock, m, body);
      if (ownerHandled) return;

      const payload = await normalizeWhatsAppMessage(sock, {

        ...m,
        messageText: body,
      });

      // Forward to FastAPI
      const apiRes = await forwardToFastAPI(payload);

      if (!apiRes) return;
      if (apiRes.status !== 'success') {
        const reason = apiRes.reason || 'unknown';
        logInfo('FastAPI ignored message.', { reason, chat: payload.chat_id });
        return;
      }

      const replyText = (apiRes.reply ?? '').toString().trim();
      if (!replyText) {
        await sock.sendMessage(payload.chat_id, { text: process.env.FASTAPI_FALLBACK_REPLY || 'Sorry! 😭' });
        return;
      }

      await sock.sendMessage(payload.chat_id, { text: replyText }, { quoted: m });
    } catch (e) {
      logError('messages.upsert handler error', { message: e?.message, stack: e?.stack });
    }
  }
}

start().catch((e) => {
  logError('Fatal start() error', { message: e?.message, stack: e?.stack });
  process.exit(1);
});

