const dotenv = require('dotenv');
dotenv.config();

const axios = require('axios');
const qrcode = require('qrcode-terminal');
const { Client, RemoteAuth } = require('whatsapp-web.js');
const { MongoStore } = require('wwebjs-mongo');
const mongoose = require('mongoose');
const NodeCache = require('node-cache');
const express = require('express');
const dns = require('dns');
dns.setServers(['8.8.8.8','8.8.4.4']);

// ---------- Dummy Server for Render ----------
const app = express();
app.get('/', (req, res) => res.send('Nezuko Bot is Awake! 🌸'));
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`[WA][INFO] Dummy server listening on port ${PORT}`));

// ---------- Config ----------
const FASTAPI_URL = process.env.FASTAPI_URL || 'http://localhost:8000';
const SESSION_NAME = process.env.SESSION_NAME || 'default_session';
const HEADLESS = String(process.env.HEADLESS || 'true').toLowerCase() === 'true';
const OWNER_NUMBER = process.env.OWNER_NUMBER || '';
const BOT_NAME = process.env.BOT_NAME || 'College Community Bot';
const BOT_PREFIX = process.env.BOT_PREFIX || '/';
const MAX_MESSAGE_LENGTH = Number(process.env.MAX_MESSAGE_LENGTH || '4000');
const MONGODB_URI = process.env.MONGODB_URI;

if (!MONGODB_URI) {
    console.error(process.env.SESSION_NAME);
    process.exit(1);
}

const WHATSAPP_API_ENDPOINT = `${FASTAPI_URL}/api/v1/whatsapp/message`;

// Cache to prevent duplicate message processing (TTL: 120 seconds)
const messageCache = new NodeCache({ stdTTL: 120, checkperiod: 15 });

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

// ---------- Rate limit (simple in-memory) ----------
const RATE_LIMIT_WINDOW_MS = 10_000;
const RATE_LIMIT_MAX = 6; // messages per window
const senderBuckets = new Map();

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

// ---------- Owner commands ----------
function isOwner(senderId) {
  if (!OWNER_NUMBER) return false;
  return senderId === `${OWNER_NUMBER}@c.us` || senderId === OWNER_NUMBER;
}

async function handleOwnerCommand(msg, text) {
  if (!isOwner(msg.from)) return false;

  const normalized = String(text || '').trim();
  const cmd = normalized.split(/\s+/)[0];

  if (cmd === '/restart' || cmd === `${BOT_PREFIX}restart`) {
    await msg.reply('Restarting session... 🔄');
    setTimeout(() => process.exit(0), 1000);
    return true;
  }

  if (cmd === '/status' || cmd === `${BOT_PREFIX}status`) {
    await msg.reply(`Status ✅\nBot: ${BOT_NAME}`);
    return true;
  }

  if (cmd === '/help' || cmd === `${BOT_PREFIX}help`) {
    await msg.reply(
      [
        `${BOT_NAME} owner commands:`,
        `- /status`,
        `- /restart`,
        `Other commands are handled via FastAPI access control.`,
      ].join('\n')
    );
    return true;
  }

  return false;
}

// ---------- Message normalization ----------
function safeSlice(s, n) {
  const str = String(s ?? '');
  if (str.length <= n) return str;
  return str.slice(0, n);
}

// Converted to async to properly fetch quoted messages without crashing
async function normalizeWhatsAppMessage(message) {
  const from = message.from; 
  const isGroup = from?.endsWith('@g.us');

  const userId = message.author || from; 
  const phoneNumber = userId?.replace('@c.us', '').replace('@g.us', '') || '';

  const contact = await message.getContact().catch(() => null);
  const chat = await message.getChat().catch(() => null);

  const senderName = contact?.name || contact?.pushname || "Unknown";
  const profileName = contact?.pushname || "";

  const groupId = isGroup ? from : null;
  const groupName = isGroup && chat ? chat.name : null;

  const body = safeSlice(message.body, MAX_MESSAGE_LENGTH);
  const messageType = message.type || (message.hasMedia ? 'media' : 'text');

  let quotedText = null;
  if (message.hasQuotedMsg) {
      try {
          const quotedMsg = await message.getQuotedMessage();
          quotedText = quotedMsg.body || "[Media/Non-text Quote]";
      } catch (e) {
          logWarn("Could not extract quoted message data");
      }
  }

  return {
    platform_id: userId || from,
    phone_number: phoneNumber,
    sender_name: senderName,
    profile_name: profileName,
    chat_id: from,
    group_id: groupId,
    group_name: groupName,
    message: body,
    quoted_message: quotedText,
    media: message.hasMedia ? { type: message.type } : null,
    location: message.location ? { lat: message.location.latitude, lng: message.location.longitude } : null,
    sticker: message.type === 'sticker' ? "yes" : null,
    voice: message.type === 'ptt' ? "yes" : null,
    timestamp: message.timestamp || Math.floor(Date.now() / 1000),
    message_type: messageType,
    is_group: isGroup,
    quoted_text: quotedText,
  };
}

// ---------- API Request with Retries ----------
async function forwardToFastAPI(payload, retries = 3) {
  const timeoutMs = Number(process.env.FASTAPI_TIMEOUT_MS || '55000');
  const fallbackReply = process.env.FASTAPI_FALLBACK_REPLY || 'Sorry! My brain is taking time. Try again in a bit 😭';

  for (let attempt = 1; attempt <= retries; attempt++) {
      try {
          const response = await axios.post(WHATSAPP_API_ENDPOINT, payload, {
              headers: { 'Content-Type': 'application/json' },
              timeout: timeoutMs,
          });
          return response.data;
      } catch (error) {
          logWarn(`FastAPI request attempt ${attempt} failed: ${error.message}`);
          
          if (error.code === 'ECONNABORTED') {
              return { __timeout: true, reply: fallbackReply };
          }
          
          if (attempt === retries) {
              logError('All retries to FastAPI exhausted.');
              return null;
          }
          
          await new Promise(res => setTimeout(res, 1000 * Math.pow(2, attempt - 1)));
      }
  }
}

// ---------- Database & WhatsApp Client Initialization ----------
mongoose.connect(MONGODB_URI).then(() => {
    logInfo('Successfully connected to MongoDB for Session Storage ✅');
    
    const store = new MongoStore({ mongoose: mongoose });

const fs = require("fs");

console.log(
  "Chrome exists:",
  fs.existsSync("/opt/render/.cache/puppeteer/chrome/linux-146.0.7680.31/chrome-linux64/chrome")
);

console.log("PUPPETEER_EXECUTABLE_PATH =", process.env.PUPPETEER_EXECUTABLE_PATH);
console.log("PUPPETEER_CACHE_DIR =", process.env.PUPPETEER_CACHE_DIR);

const client = new Client({
    const client = new Client({
      authStrategy: new RemoteAuth({
        store: store,
        backupSyncIntervalMs: 60000, // Saves session to DB every 60 seconds
        clientId: SESSION_NAME
      }),
      puppeteer: {
    headless: HEADLESS,
    args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu"
    ]
      },
      restartOnAuthFail: true,
    });

    client.on('qr', (qr) => {
      logInfo('QR received. Scan it once to authenticate.');
      qrcode.generate(qr, { small: true });
    });

    client.on('ready', () => {
      logInfo('Client is ready and linked to MongoDB! ✅');
    });

    client.on('remote_session_saved', () => {
      logInfo('WhatsApp session backed up to MongoDB successfully.');
    });

    client.on('auth_failure', (msg) => {
      logError('Auth failure. Session may be expired. QR will be required again.', msg);
    });

    client.on('disconnected', (reason) => {
      logWarn('Disconnected. whatsapp-web.js will attempt reconnect.', { reason });
      setTimeout(() => {
        client.initialize().catch(err => logError('Reconnect failed:', err));
      }, 5000);
    });

    client.on('message_create', async (message) => {
      console.log(`\n[DEBUG] Raw message detected!`);
      console.log(`[DEBUG] From: ${message.from} | fromMe: ${message.fromMe} | Body: "${message.body}"`);

      try {
        if (message.fromMe) {
            console.log(`[DEBUG] Ignored: Message was sent by the bot (fromMe).`);
            return;
        }

        const from = message.from;
        if (!from) return;

        if (from.endsWith('@broadcast') || from.endsWith('@status')) return;

        if (messageCache.has(message.id._serialized)) return;
        messageCache.set(message.id._serialized, true);

        const body = message.body || '';
        if (!body && !message.hasMedia) return;

        const senderId = message.author || message.from;
        if (!senderId) return;

        if (isRateLimited(senderId)) {
          logWarn('Rate limited sender:', senderId);
          return;
        }

        if (body) {
          const ownerHandled = await handleOwnerCommand(message, body);
          if (ownerHandled) return;
        }

        const payload = await normalizeWhatsAppMessage(message);
        const chat = await message.getChat().catch(() => null);

        if (chat) await chat.sendStateTyping();

        const startTime = Date.now();
        logInfo(`Incoming payload from ${payload.sender_name}`, { chat_id: payload.chat_id });

        const apiRes = await forwardToFastAPI(payload);
        
        if (chat) await chat.clearState();

        if (!apiRes) return;

        if (apiRes.status !== 'success') {
          const reason = apiRes.reason || 'unknown';
          logInfo('FastAPI ignored message.', { reason, chat: payload.chat_id });
          return;
        }

        const replyText = (apiRes.reply ?? '').toString().trim();
        
        if (!replyText) {
          logWarn('FastAPI returned empty reply; sending fallback.');
          const fallback = process.env.FASTAPI_FALLBACK_REPLY || 'Sorry! I had trouble generating a reply right now. 😭';
          await message.reply(fallback);
          return;
        }

        await message.reply(replyText);
        logInfo(`Replied to user successfully in ${Date.now() - startTime}ms.`);

      } catch (err) {
        logError('Message handler error', {
          message: err?.message,
          stack: err?.stack,
        });
      }
    });

    // ---------- Global Crash Protection ----------
    process.on('uncaughtException', (err) => {
        logError('CRITICAL Uncaught Exception (Process saved):', err);
    });

    process.on('unhandledRejection', (reason, promise) => {
        logError('CRITICAL Unhandled Rejection at:', promise, 'reason:', reason);
    });

    process.on('SIGINT', async () => {
      logInfo('SIGINT received. Closing client...');
      try {
        await client.destroy();
      } catch (e) {}
      process.exit(0);
    });

    client.initialize();
}).catch(err => {
    console.error('[WA][ERROR] Failed to connect to MongoDB', err);
});
