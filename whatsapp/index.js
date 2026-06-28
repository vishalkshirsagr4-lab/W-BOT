const dotenv = require('dotenv');
dotenv.config();

const axios = require('axios');
const qrcode = require('qrcode-terminal');
const { Client, LocalAuth } = require('whatsapp-web.js');
const NodeCache = require('node-cache');

const fs = require('fs');
const path = require('path');

// Auto-delete the browser lock file if it was left behind
function cleanSessionLock() {
    const lockPath = path.join(__dirname, '.wwebjs_auth', `session-${SESSION_NAME}`, 'SingletonLock');
    if (fs.existsSync(lockPath)) {
        try {
            fs.unlinkSync(lockPath);
            console.log('[WA][INFO] Cleared leftover browser lock file.');
        } catch (err) {
            console.error('[WA][ERROR] Could not clear lock file:', err);
        }
    }
}

// ---------- Config ----------
const FASTAPI_URL = process.env.FASTAPI_URL || 'http://localhost:8000';
const SESSION_NAME = process.env.SESSION_NAME || 'default_session';
const HEADLESS = String(process.env.HEADLESS || 'true').toLowerCase() === 'true';
const OWNER_NUMBER = process.env.OWNER_NUMBER || '';
const BOT_NAME = process.env.BOT_NAME || 'College Community Bot';
const BOT_PREFIX = process.env.BOT_PREFIX || '/';
const MAX_MESSAGE_LENGTH = Number(process.env.MAX_MESSAGE_LENGTH || '4000');

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

  // Correctly extract quoted text using official API
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
          
          // Exponential backoff
          await new Promise(res => setTimeout(res, 1000 * Math.pow(2, attempt - 1)));
      }
  }
}

// ---------- WhatsApp client ----------
const client = new Client({
  authStrategy: new LocalAuth({ clientId: SESSION_NAME }),
  puppeteer: {
    headless: HEADLESS,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-accelerated-2d-rendering',
      '--no-first-run',
      '--no-zygote',
      '--single-process',
      '--disable-gpu'
    ],
  },
  restartOnAuthFail: true,
});

client.on('qr', (qr) => {
  logInfo('QR received. Scan it once to authenticate.');
  qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
  logInfo('Client is ready ✅');
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

    // 1. Critical Stop: Prevent bot from replying to itself
    if (message.fromMe) {
        console.log(`[DEBUG] Ignored: Message was sent by the bot (fromMe).`); // <-- Add this to see if it drops here
        return;
    }

    const from = message.from;
    if (!from) return;

    // 2. Filter Status and Broadcasts
    if (from.endsWith('@broadcast') || from.endsWith('@status')) return;

    // 3. Duplicate Message Protection
    if (messageCache.has(message.id._serialized)) return;
    messageCache.set(message.id._serialized, true);

    const body = message.body || '';
    if (!body && !message.hasMedia) return;

    const senderId = message.author || message.from;
    if (!senderId) return;

    // 4. Rate Limiting
    if (isRateLimited(senderId)) {
      logWarn('Rate limited sender:', senderId);
      return;
    }

    // 5. Owner Commands
    if (body) {
      const ownerHandled = await handleOwnerCommand(message, body);
      if (ownerHandled) return;
    }

    // 6. Normalize Payload
    const payload = await normalizeWhatsAppMessage(message);
    const chat = await message.getChat().catch(() => null);

    // 7. UX: Start Typing Indicator
    if (chat) await chat.sendStateTyping();

    const startTime = Date.now();
    logInfo(`Incoming payload from ${payload.sender_name}`, { chat_id: payload.chat_id });

    // 8. Send to API
    const apiRes = await forwardToFastAPI(payload);
    
    // 9. UX: Stop Typing Indicator
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

    // 10. Final Reply
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

// Clean up any old locks before starting
cleanSessionLock();
client.initialize();