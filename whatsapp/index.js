const dotenv = require('dotenv');
dotenv.config();

const axios = require('axios');
const qrcode = require('qrcode-terminal');
const { Client, LocalAuth } = require('whatsapp-web.js');

// ---------- Config ----------
const FASTAPI_URL = process.env.FASTAPI_URL || 'http://localhost:8000';
const SESSION_NAME = process.env.SESSION_NAME || 'default_session';
const HEADLESS = String(process.env.HEADLESS || 'true').toLowerCase() === 'true';
const OWNER_NUMBER = process.env.OWNER_NUMBER || '';
const BOT_NAME = process.env.BOT_NAME || 'College Community Bot';
const BOT_PREFIX = process.env.BOT_PREFIX || '/';
const MAX_MESSAGE_LENGTH = Number(process.env.MAX_MESSAGE_LENGTH || '4000');

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

// ---------- Owner commands (lightweight local handling) ----------
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

function extractQuotedText(quotedMsg) {
  if (!quotedMsg) return null;
  return quotedMsg.body || null;
}

function normalizeWhatsAppMessage(message) {
  const from = message.from; // group@g.us or number@c.us
  const isGroup = from?.endsWith('@g.us');

  const userId = message.author || from; // author for groups
  const phoneNumber = userId?.replace('@c.us', '').replace('@g.us', '') || '';

  const senderName = message._data?.notifyName || message._data?.author || '';
  const profileName = message._data?.notifyName || senderName;

  const groupId = isGroup ? from : null;
  const groupName = message._data?.chatName || null;

  const body = safeSlice(message.body, MAX_MESSAGE_LENGTH);
  const messageType = message.type || (message.hasMedia ? 'media' : 'text');

  return {
    platform_id: userId || from,
    phone_number: phoneNumber,

    sender_name: senderName,
    profile_name: profileName,

    chat_id: from,
    group_id: groupId,
    group_name: groupName,

    message: body,
    quoted_message: message._data?.quotedMsg?.body || null,

    media: null,
    location: message.location || null,
    sticker: message.stickerId || null,
    voice: message.type === 'ptt' ? body : null,
    timestamp: message.timestamp || Math.floor(Date.now() / 1000),

    message_type: messageType,
    is_group: isGroup,

    quoted_text: extractQuotedText(message._data?.quotedMsg),
  };
}

async function forwardToFastAPI(payload) {
  const timeoutMs = Number(process.env.FASTAPI_TIMEOUT_MS || '55000');
  const fallbackReply =
    process.env.FASTAPI_FALLBACK_REPLY ||
    'Sorry! My brain is taking time. Try again in a bit 😭';

  const timeoutPromise = new Promise((resolve) => {
    setTimeout(() => {
      resolve({ __timeout: true, reply: fallbackReply });
    }, timeoutMs);
  });

  const requestPromise = axios
    .post(WHATSAPP_API_ENDPOINT, payload, {
      headers: { 'Content-Type': 'application/json' },
      timeout: timeoutMs,
    })
    .then((res) => res.data);

  return await Promise.race([requestPromise, timeoutPromise]);
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
});

client.on('message_create', async (message) => {
  try {
    const from = message.from;
    if (!from) return;

    if (from.endsWith('@broadcast')) return;
    if (from.endsWith('@status')) return;

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

    const payload = normalizeWhatsAppMessage(message);

    const apiRes = await forwardToFastAPI(payload);
    logInfo('FastAPI response received.', apiRes);

    if (!apiRes) return;

    if (apiRes.status !== 'success') {
      const reason = apiRes.reason || 'unknown';
      logInfo('Ignored message.', {
        reason,
        chat: payload.chat_id,
        sender: payload.sender_name,
      });
      return;
    }

    const replyText = (apiRes.reply ?? '').toString().trim();
    logInfo('About to reply.', { replyLen: replyText.length });

    if (!replyText) {
      logWarn('FastAPI returned empty reply; sending fallback.');
      const fallback =
        process.env.FASTAPI_FALLBACK_REPLY ||
        'Sorry! I had trouble generating a reply right now. 😭';
      await message.reply(fallback);
      return;
    }

    await message.reply(replyText);
    logInfo('Replied to user.');
  } catch (err) {
    logError('Message handler error', {
      message: err?.message,
      stack: err?.stack,
    });
  }
});

client.initialize();

process.on('SIGINT', async () => {
  logInfo('SIGINT received. Closing client...');
  try {
    await client.destroy();
  } catch (e) {}
  process.exit(0);
});

