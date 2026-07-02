const dotenv = require('dotenv');
dotenv.config();

const axios = require('axios');
const express = require('express');
const dns = require('dns');
const fs = require('fs');
const path = require('path');
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
app.get('/health', (req, res) => res.json({ status: 'ok', uptime: process.uptime() }));
const PORT = process.env.PORT || 3000;
const server = app.listen(PORT, () => console.log(`[WA][INFO] Dummy server listening on port ${PORT}`));

// QR storage and expiry
let currentQR = null; // { dataUrl, ts }
const QR_EXPIRE_MS = Number(process.env.QR_EXPIRE_MS || 1000 * 60 * 5); // 5 minutes

// QR API
app.get('/qr', (req, res) => {
  res.set('Cache-Control', 'no-store');
  logInfo('QR API requested', { ip: req.ip });
  if (socket && lastSocketHealth?.state === 'open') return res.json({ connected: true });
  if (currentQR && Date.now() - currentQR.ts < QR_EXPIRE_MS) {
    logInfo('QR served', { ageMs: Date.now() - currentQR.ts });
    res.json({ connected: false, qr: currentQR.dataUrl });
  } else {
    if (currentQR) logInfo('QR expired', { ageMs: Date.now() - currentQR.ts });
    res.json({ connected: false });
  }
});

// QR Page
app.get('/qr-page', (req, res) => {
  res.set('Cache-Control', 'no-store');
  logInfo('QR page requested', { ip: req.ip });
  const html = `<!doctype html>
  <html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>WhatsApp QR - Nezuko</title>
    <style>
      :root{color-scheme:dark;}
      body{background:#0b1020;color:#e6eef8;font-family:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
      .card{max-width:760px;width:100%;padding:24px;border-radius:12px;background:linear-gradient(180deg,rgba(255,255,255,0.02),rgba(255,255,255,0.01));box-shadow:0 6px 24px rgba(2,6,23,0.6);text-align:center}
      h1{margin:0 0 8px;font-size:20px}
      p{margin:0 0 16px;color:#9fb0d6}
      .qr{width:320px;height:320px;margin:12px auto;background:#fff;padding:12px;border-radius:8px}
      img.qrimg{width:100%;height:100%;object-fit:contain;display:block}
      .small{font-size:13px;color:#8fa3cc;margin-top:8px}
      @media(max-width:480px){.qr{width:260px;height:260px}}
    </style>
  </head>
  <body>
    <div class="card">
      <h1 id="status">Waiting for QR...</h1>
      <p id="sub">Open this page on your phone to scan the QR</p>
      <div class="qr" id="qrbox"><img class="qrimg" id="qrimg" alt="QR code"/></div>
      <div class="small">Auto-refreshes every 2s · Dark theme · No cache</div>
    </div>
    <script>
      let last = null;
      async function fetchQR(){
        try{
          const r = await fetch('/qr',{cache:'no-store'});
          const j = await r.json();
          if(j.connected){
            document.getElementById('status').textContent = 'WhatsApp Connected';
            document.getElementById('qrimg').style.display = 'none';
            return;
          }
          if(j.qr){
            if(last !== j.qr){
              last = j.qr;
              document.getElementById('qrimg').src = j.qr;
              document.getElementById('qrimg').style.display = 'block';
              document.getElementById('status').textContent = 'Scan QR with WhatsApp';
            }
          } else {
            document.getElementById('status').textContent = 'Waiting for QR...';
            document.getElementById('qrimg').style.display = 'none';
          }
        }catch(err){
          console.error(err);
        }
      }
      fetchQR();
      setInterval(fetchQR,2000);
    </script>
  </body>
  </html>`;
  res.type('html').send(html);
});

// ---------- Config ----------
const FASTAPI_URL = process.env.FASTAPI_URL || '';

const OWNER_NUMBER = process.env.OWNER_NUMBER || '';
const BOT_NAME = process.env.BOT_NAME || 'College Community Bot';
const BOT_PREFIX = process.env.BOT_PREFIX || '/';
const MAX_MESSAGE_LENGTH = Number(process.env.MAX_MESSAGE_LENGTH || '4000');

// Baileys auth state directory (multi-file)
const AUTH_DIR = path.resolve(process.env.WA_AUTH_DIR || './.baileys_auth');
const AUTH_STATE_FILE = path.join(AUTH_DIR, 'auth-state.json');

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

const RECONNECT_BASE_DELAY_MS = Number(process.env.RECONNECT_BASE_DELAY_MS || '3000');
const RECONNECT_MAX_DELAY_MS = Number(process.env.RECONNECT_MAX_DELAY_MS || '60000');
const RECONNECT_MAX_ATTEMPTS = Number(process.env.RECONNECT_MAX_ATTEMPTS || '20');
const HEARTBEAT_INTERVAL_MS = Number(process.env.HEARTBEAT_INTERVAL_MS || '30000');
const WHATSAPP_API_ENDPOINT = FASTAPI_URL ? `${FASTAPI_URL}/api/v1/whatsapp/message` : '';
const FASTAPI_HTTP_TIMEOUT_MS = Number(process.env.FASTAPI_TIMEOUT_MS || '8000');
const fastApiHttpClient = axios.create({
  timeout: FASTAPI_HTTP_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
});

let socket = null;
let authState = null;
let reconnectTimer = null;
let heartbeatTimer = null;
let reconnectAttempts = 0;
let isReconnecting = false;
let isShuttingDown = false;
let lastSocketHealth = null;
let heartbeatUnhealthyCount = 0;
const HEARTBEAT_UNHEALTHY_THRESHOLD = Number(process.env.HEARTBEAT_UNHEALTHY_THRESHOLD || '2');

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

function getMemoryUsage() {
  const usage = process.memoryUsage();
  return {
    rssMB: Math.round(usage.rss / 1024 / 1024),
    heapUsedMB: Math.round(usage.heapUsed / 1024 / 1024),
    heapTotalMB: Math.round(usage.heapTotal / 1024 / 1024),
  };
}

function getCpuUsage() {
  const usage = process.cpuUsage();
  return {
    userMS: usage.user,
    systemMS: usage.system,
  };
}

function clearReconnectTimer() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function clearHeartbeatTimer() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
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
  if (!m.quotedMsg || !m.quotedMsg.message) return null;

  const q = m.quotedMsg.message;
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

  const senderName = 'Unknown';
  const profileName = '';

  const quotedText = normalizeQuotedText(m);

  const body = safeSlice(m.messageText || m.message?.conversation || '', MAX_MESSAGE_LENGTH);

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
  if (!WHATSAPP_API_ENDPOINT) {
    return { status: 'error', reason: 'FASTAPI_URL missing on Render', reply: (process.env.FASTAPI_FALLBACK_REPLY || 'FASTAPI_URL is not configured on this server. ❌') };
  }

  const timeoutMs = Number(process.env.FASTAPI_TIMEOUT_MS || '8000');

  const cacheKey = payload?.platform_id ? `${payload.platform_id}|${payload.message}|${payload.is_group}` : null;

  if (cacheKey) {
    const cached = aiResponseCache.get(cacheKey);
    if (cached) return cached;
  }

  const fallbackReply = process.env.FASTAPI_FALLBACK_REPLY || 'Sorry! My brain is taking time. Try again in a bit 😭';

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

        const response = await fastApiHttpClient.post(WHATSAPP_API_ENDPOINT, payload, {
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

async function ensureAuthDir() {
  try {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    const tempFile = path.join(AUTH_DIR, '.write-test');
    fs.writeFileSync(tempFile, 'ok');
    fs.unlinkSync(tempFile);
  } catch (error) {
    logError('Auth directory unavailable', { error: error?.message || error });
  }
}

async function saveStateSafely(saveCreds) {
  try {
    if (typeof saveCreds === 'function') {
      await saveCreds();
    }
    if (authState?.creds) {
      const statePath = path.join(AUTH_DIR, 'creds.json');
      fs.writeFileSync(statePath, JSON.stringify(authState.creds, null, 2));
    }
  } catch (error) {
    logError('Failed to save auth state', { error: error?.message || error });
  }
}

function shouldReconnect(reason, lastDisconnect) {
  if (isShuttingDown) return false;
  // If we're still starting or connecting, avoid reconnect storms
  if (lastSocketHealth?.state === 'starting' || lastSocketHealth?.state === 'connecting') return false;
  // If a QR is active and not expired, wait for scan before reconnecting
  if (currentQR && Date.now() - currentQR.ts < QR_EXPIRE_MS) return false;
  if (reason === 'logout') return false;
  if (lastDisconnect?.error?.output?.statusCode === DisconnectReason.loggedOut) return false;
  if (lastDisconnect?.error?.output?.statusCode === DisconnectReason.connectionReplaced) return true;
  if (reason === 'connection_replaced') return true;
  if (reason === 'network_lost' || reason === 'stream_error' || reason === 'restart_required' || reason === 'timeout') return true;
  return true;
}

function getReconnectDelay(attempt) {
  const capped = Math.min(RECONNECT_BASE_DELAY_MS * 2 ** (attempt - 1), RECONNECT_MAX_DELAY_MS);
  return capped + Math.floor(Math.random() * 1000);
}

async function stopSocket(reason = 'shutdown') {
  clearReconnectTimer();
  clearHeartbeatTimer();

  if (socket) {
    try {
      logWarn('Stopping existing socket', { reason, id: socket?.user?.id, state: lastSocketHealth });
      try {
        socket.ev.removeAllListeners?.();
      } catch (err) {
        logWarn('Failed to remove event listeners cleanly', { error: err?.message || err });
      }

      try {
        // prefer graceful close; if not available, terminate
        if (socket.ws && typeof socket.ws.close === 'function') await socket.ws.close();
        else if (socket.ws && typeof socket.ws.terminate === 'function') socket.ws.terminate();
      } catch (err) {
        logWarn('Socket close warning', { error: err?.message || err });
      }
    } catch (error) {
      logWarn('Socket stop outer warning', { error: error?.message || error });
    }
    socket = null;
  }
}

async function reconnectSocket(reason = 'unknown', lastDisconnect) {
  if (isShuttingDown) {
    logInfo('Reconnect skipped due to shutdown', { reason });
    return;
  }

  // If socket appears healthy, skip reconnect
  if (socket && (socket.ws?.readyState === 1 || lastSocketHealth?.state === 'open' || socket?.user?.id)) {
    logInfo('Reconnect skipped: socket already healthy', { reason, readyState: socket.ws?.readyState, lastState: lastSocketHealth?.state });
    return;
  }

  if (!shouldReconnect(reason, lastDisconnect)) {
    logInfo('Reconnect skipped by shouldReconnect', { reason, statusCode: lastDisconnect?.error?.output?.statusCode });
    return;
  }

  if (isReconnecting) {
    logInfo('Reconnect already in progress; skipping duplicate', { reason });
    return;
  }

  isReconnecting = true;
  reconnectAttempts += 1;
  const attempt = reconnectAttempts;
  const delay = getReconnectDelay(attempt);

  logWarn('Reconnect scheduled', { attempt, reason, delayMs: delay, memory: getMemoryUsage() });

  clearReconnectTimer();
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null;
    try {
      logInfo('Reconnect started', { attempt, reason });
      await stopSocket('reconnect');
      await start();
      isReconnecting = false;
    } catch (error) {
      logError('Reconnect failed', { attempt, error: error?.message || error, stack: error?.stack });
      isReconnecting = false;
      if (attempt < RECONNECT_MAX_ATTEMPTS) {
        reconnectSocket('retry_failed', null);
      } else {
        logError('Max reconnect attempts reached; exiting', { attempt });
        process.exit(1);
      }
    }
  }, delay);
}

async function start() {
  if (isShuttingDown) return;
  if (socket) {
    logInfo('Socket already exists; skipping duplicate start');
    return;
  }

  logInfo('Starting Baileys socket...', { authDir: AUTH_DIR, memory: getMemoryUsage() });

  await ensureAuthDir();

  const logger = pino({ level: process.env.BAILEYS_LOG_LEVEL || 'info' });

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  authState = state;

  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    logger,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    version,
    browser: Browsers.ubuntu('Chrome'),
    syncFullHistory: false,
    markOnlineOnConnect: true,
  });

  socket = sock;
  isReconnecting = false;
  lastSocketHealth = { timestamp: Date.now(), state: 'starting' };

  sock.ev.on('creds.update', async () => {
    try {
      await saveCreds();
      logInfo('[WA][AUTH] Credentials saved');
    } catch (error) {
      logError('[WA][AUTH] Credentials save failed', { error: error?.message || error });
    }
  });

  // Consolidated connection.update handler with detailed logging
  sock.ev.on('connection.update', async (update) => {
    try {
      const { connection, lastDisconnect, qr, receivedPendingNotifications, isOnline, isNewLogin } = update;

      logInfo('[WA][EVENT] connection.update', {
        connection,
        lastDisconnect: lastDisconnect ? { message: lastDisconnect?.error?.message, output: lastDisconnect?.error?.output } : null,
        wsReadyState: socket?.ws?.readyState,
        userId: socket?.user?.id,
      });

      if (qr) {
        try {
          const qrcode = require('qrcode');
          const dataUrl = await qrcode.toDataURL(qr, { errorCorrectionLevel: 'M', margin: 1, scale: 6 });
          // store QR until connection or newer QR
          const now = Date.now();
          const prev = currentQR;
          currentQR = { dataUrl, ts: now };
          logInfo('[WA][QR] QR generated', { ageMs: 0, preview: safeSlice(dataUrl, 200) });
          if (!prev || prev.dataUrl !== dataUrl) logInfo('[WA][QR] QR updated', { ageMs: 0 });
        } catch (error) {
          logError('[WA][QR] QR handling error', { error: error?.message || error });
        }
      }

      if (connection === 'open') {
        reconnectAttempts = 0;
        isReconnecting = false;
        heartbeatUnhealthyCount = 0;
        lastSocketHealth = { timestamp: Date.now(), state: 'open' };
        logInfo('[WA][STATE] Connected', { isOnline, isNewLogin, receivedPendingNotifications });
        if (currentQR) {
          logInfo('[WA][QR] Clearing stored QR due to successful connection');
          currentQR = null;
        }
        return;
      }

      if (connection === 'close') {
        const statusCode = lastDisconnect?.error?.output?.statusCode || lastDisconnect?.error?.output?.status || null;
        const reason = lastDisconnect?.error?.output?.payload?.message || lastDisconnect?.error?.message || 'unknown';
        const shouldReconnectNow = shouldReconnect('connection_closed', lastDisconnect);
        logWarn('[WA][STATE] Disconnected', { shouldReconnectNow, statusCode, reason, lastDisconnect });
        if (shouldReconnectNow) {
          await reconnectSocket('connection_closed', lastDisconnect);
        }
      }
    } catch (err) {
      logError('connection.update handler error', { error: err?.message || err, stack: err?.stack });
    }
  });

  sock.ev.on('messages.upsert', (event) => {
    setImmediate(() => {
      handleUpsertEvent(sock, event).catch((error) => {
        logError('messages.upsert outer handler error', { message: error?.message, stack: error?.stack });
      });
    });
  });

  sock.ev.on('ws.close', () => {
    logWarn('[WA][STATE] WebSocket closed', { wsReadyState: socket?.ws?.readyState });
    reconnectSocket('websocket_closed', null).catch((error) => logError('ws.close reconnect failed', { error: error?.message || error }));
  });

  sock.ev.on('ws.error', (error) => {
    logError('[WA][STATE] WebSocket error', { error: error?.message || error });
    reconnectSocket('websocket_error', null).catch((error) => logError('ws.error reconnect failed', { error: error?.message || error }));
  });

  sock.ev.on('stream.error', (error) => {
    logError('[WA][STATE] Stream error', { error: error?.message || error });
    reconnectSocket('stream_error', null).catch((error) => logError('stream.error reconnect failed', { error: error?.message || error }));
  });

  sock.ev.on('connection.update', (update) => {
    if (update?.connection === 'connecting') logInfo('[WA][STATE] Connecting');
  });

  sock.ev.on('creds.update', () => {
    logInfo('[WA][AUTH] Auth state changed');
  });

  if (!heartbeatTimer) {
    heartbeatTimer = setInterval(() => {
      const now = Date.now();

      const wsReady = socket?.ws?.readyState;
      const hasUser = Boolean(socket?.user?.id);
      // Consider healthy if websocket is OPEN, or we recently had an 'open' connection state, or socket reports user id
      const healthy = Boolean(socket && (wsReady === 1 || lastSocketHealth?.state === 'open' || hasUser));

      if (!healthy) {
        heartbeatUnhealthyCount += 1;
        logWarn('[WA][HEARTBEAT] Unhealthy check', {
          attempt: heartbeatUnhealthyCount,
          threshold: HEARTBEAT_UNHEALTHY_THRESHOLD,
          hasSocket: Boolean(socket),
          wsReadyState: wsReady,
          lastState: lastSocketHealth?.state,
          memory: getMemoryUsage(),
          cpu: getCpuUsage(),
        });

        if (heartbeatUnhealthyCount >= HEARTBEAT_UNHEALTHY_THRESHOLD) {
          logWarn('[WA][HEARTBEAT] Threshold reached; scheduling reconnect', { heartbeatUnhealthyCount });
          heartbeatUnhealthyCount = 0;
          reconnectSocket('heartbeat_unhealthy', null).catch((error) => logError('heartbeat reconnect failed', { error: error?.message || error }));
        }
        return;
      }

      // healthy
      heartbeatUnhealthyCount = 0;
      logInfo('[WA][HEARTBEAT] Healthy', { wsReadyState: wsReady, memory: getMemoryUsage(), cpu: getCpuUsage() });
      lastSocketHealth = { timestamp: now, state: 'healthy' };
    }, HEARTBEAT_INTERVAL_MS);
  }

  async function handleUpsertEvent(sock, event) {
    try {
      const m = event?.messages?.[0];

      if (!m || !m.key) return;

      const msgId = m.key?.id;
      if (!msgId) return;
      if (messageCache.has(msgId)) return;
      messageCache.set(msgId, true);

      if (m.key?.fromMe) return;

      const fromJid = m.key.remoteJid;
      if (!fromJid) return;
      if (fromJid.endsWith('@broadcast') || fromJid.endsWith('broadcast')) return;

      const body = extractMessageText(m);
      if (!body && !m.message?.imageMessage && !m.message?.videoMessage) return;

      const senderId = m.key.participant || fromJid;
      const textForTrigger = String(body ?? '').toLowerCase();
      if (!textForTrigger.includes('nezuko')) {
        logInfo('[SKIP] No trigger', { sender: senderId });
        return;
      }
      logInfo('[TRIGGERED] Nezuko activated', { sender: senderId });

      if (isRateLimited(senderId)) {
        logWarn('Rate limited sender:', senderId);
        return;
      }

      let pendingAckMessage = null;

      try {
        pendingAckMessage = await sock.sendMessage(fromJid, { text: '⏳' });
        logInfo('[WA][OUTBOUND] Ack sent', { chat: fromJid });
      } catch (error) {
        logWarn('[WA][OUTBOUND] Ack failed', { error: error?.message || error });
      }

      const ownerHandled = await handleOwnerCommand(sock, m, body);
      if (ownerHandled) return;

      const payload = await normalizeWhatsAppMessage(sock, { ...m, messageText: body });
      logInfo('[WA][INBOUND] Message received', { chat: payload.chat_id, sender: payload.platform_id, length: payload.message?.length || 0 });

      const apiRes = await forwardToFastAPI(payload);
      if (!apiRes) return;
      if (apiRes.status !== 'success') {
        const reason = apiRes.reason || 'unknown';
        logInfo('FastAPI ignored message.', { reason, chat: payload.chat_id });
        return;
      }

      const replyText = (apiRes.reply ?? '').toString().trim();

      try {
        if (pendingAckMessage?.key) {
          await sock.sendMessage(fromJid, { delete: pendingAckMessage.key });
          logInfo('[WA][OUTBOUND] Ack deleted', { chat: fromJid });
        }
      } catch (deleteErr) {
        logWarn('Failed to delete pending ack message', { error: deleteErr?.message || deleteErr });
      }

      if (!replyText) {
        await sock.sendMessage(payload.chat_id, { text: process.env.FASTAPI_FALLBACK_REPLY || 'Sorry! 😭' });
        return;
      }

      await sock.sendMessage(payload.chat_id, { text: replyText }, { quoted: m });
      logInfo('[WA][OUTBOUND] Reply sent', { chat: payload.chat_id, length: replyText.length });
    } catch (error) {
      logError('messages.upsert handler error', { message: error?.message, stack: error?.stack });
    }
  }
}

process.on('uncaughtException', (error) => {
  logError('Unhandled uncaughtException', { message: error?.message, stack: error?.stack });
  reconnectSocket('uncaught_exception', null).catch((reconnectError) => logError('uncaughtException reconnect failed', { error: reconnectError?.message || reconnectError }));
});

process.on('unhandledRejection', (reason) => {
  logError('UnhandledPromiseRejection', { reason: reason?.message || reason });
  reconnectSocket('unhandled_rejection', null).catch((error) => logError('unhandledRejection reconnect failed', { error: error?.message || error }));
});

process.on('SIGINT', async () => {
  isShuttingDown = true;
  logInfo('SIGINT received; shutting down gracefully');
  try {
    await stopSocket('sigint');
    if (server && typeof server.close === 'function') {
      server.close(() => logInfo('HTTP server closed (SIGINT)'));
    }
  } catch (err) {
    logError('Error during SIGINT shutdown', { error: err?.message || err });
  }
  process.exit(0);
});

process.on('SIGTERM', async () => {
  isShuttingDown = true;
  logInfo('SIGTERM received; shutting down gracefully');
  try {
    await stopSocket('sigterm');
    if (server && typeof server.close === 'function') {
      server.close(() => logInfo('HTTP server closed (SIGTERM)'));
    }
  } catch (err) {
    logError('Error during SIGTERM shutdown', { error: err?.message || err });
  }
  process.exit(0);
});

start().catch((error) => {
  logError('Fatal start() error', { message: error?.message, stack: error?.stack });
  reconnectSocket('startup_failed', null).catch((reconnectError) => logError('Startup reconnect failed', { error: reconnectError?.message || reconnectError }));
});
