const test = require('node:test');
const assert = require('node:assert/strict');

const {
  createMessageDeduper,
  createMessageLoopGuard,
  normalizeMessagePayload,
  shouldProcessMessage,
  isTransientFailure,
  getBackoffDelay,
  isGlobalCommand,
} = require('../src/bridge-utils');
const FastApiClient = require('../src/fastapi');
const WhatsAppBridge = require('../src/whatsapp-bridge');
const BaileysClient = require('../src/baileys-client');

test('normalizeMessagePayload maps a web.js message into the bridge contract', () => {
  const message = {
    id: { _serialized: 'msg-1' },
    from: '919999999999@c.us',
    body: 'nezuko hello',
    type: 'chat',
    timestamp: 1710000000,
    fromMe: false,
    isStatus: false,
    isBroadcast: false,
    hasMedia: false,
    quotedMsg: {
      body: 'quoted text',
    },
  };

  const normalized = normalizeMessagePayload(message);

  assert.equal(normalized.platform_id, '919999999999@c.us');
  assert.equal(normalized.phone_number, '919999999999');
  assert.equal(normalized.chat_id, '919999999999@c.us');
  assert.equal(normalized.message, 'nezuko hello');
  assert.equal(normalized.quoted_text, 'quoted text');
  assert.equal(normalized.message_type, 'chat');
  assert.equal(normalized.is_group, false);
});

test('message deduper ignores duplicate ids and allows new ones', () => {
  const deduper = createMessageDeduper(10, 1000);

  assert.equal(deduper.shouldProcess('msg-1'), true);
  assert.equal(deduper.shouldProcess('msg-1'), false);
  assert.equal(deduper.shouldProcess('msg-2'), true);
});

test('status and own messages are ignored before processing', () => {
  assert.equal(shouldProcessMessage({ fromMe: true, isStatus: false, isBroadcast: false }), false);
  assert.equal(shouldProcessMessage({ fromMe: false, isStatus: true, isBroadcast: false }), false);
  assert.equal(shouldProcessMessage({ fromMe: false, isStatus: false, isBroadcast: true }), false);
  assert.equal(shouldProcessMessage({ fromMe: false, isStatus: false, isBroadcast: false }), true);
});

test('self messages are allowed when explicitly enabled for development testing', () => {
  const selfMessage = { fromMe: true, isStatus: false, isBroadcast: false, body: 'nezuko help' };
  assert.equal(shouldProcessMessage(selfMessage, { allowSelfMessages: false }), false);
  assert.equal(shouldProcessMessage(selfMessage, { allowSelfMessages: true }), true);
});

test('normalized payloads with a message field are processed correctly', () => {
  const normalizedMessage = { fromMe: true, isStatus: false, isBroadcast: false, message: 'Nezuko help' };
  assert.equal(shouldProcessMessage(normalizedMessage, { allowSelfMessages: true }), true);
});

test('cricket slash commands are recognized as global commands', () => {
  for (const command of [
    '/live',
    '/LIVE',
    '  /cricket  ',
    '/score',
    '/score india',
    '/cricket subscribe',
    '/cricket subscribe india',
    '/cricket unsubscribe',
  ]) {
    assert.equal(isGlobalCommand(command), true, command);
  }
  assert.equal(isGlobalCommand('/help'), true);
  assert.equal(isGlobalCommand('/game meme'), true);
});

test('ordinary messages are not recognized as global commands', () => {
  assert.equal(isGlobalCommand('hello'), false);
  assert.equal(isGlobalCommand('what is the weather'), false);
  assert.equal(isGlobalCommand('live cricket'), false);
  assert.equal(isGlobalCommand('/unknown'), false);
});

test('loop guard suppresses repeated inbound messages and self-replies', () => {
  const guard = createMessageLoopGuard(5_000);
  assert.equal(guard.shouldProcess('Nezuko help', 'chat-1', 'user-1'), true);
  assert.equal(guard.shouldProcess('Nezuko help', 'chat-1', 'user-1'), false);
  guard.markOutbound('chat-1', 'Nezuko commands');
  assert.equal(guard.shouldProcess('Nezuko commands', 'chat-1', 'user-1'), false);
});

test('transient failures are retried and timeouts use backoff', () => {
  assert.equal(isTransientFailure({ code: 'ECONNRESET' }), true);
  assert.equal(isTransientFailure({ response: { status: 429 } }), true);
  assert.equal(isTransientFailure({ response: { status: 500 } }), true);
  assert.equal(isTransientFailure({ response: { status: 400 } }), false);
  assert.equal(getBackoffDelay(1), 250);
  assert.equal(getBackoffDelay(3), 1000);
});

test('FastApiClient retries transient failures before falling back', async () => {
  const client = new FastApiClient();
  let attempts = 0;

  client.client.post = async () => {
    attempts += 1;
    if (attempts < 2) {
      throw { code: 'ECONNRESET' };
    }

    return { data: { status: 'success', reply: 'ok' } };
  };

  const result = await client.forward({ message: 'hello' });

  assert.equal(attempts, 2);
  assert.equal(result.status, 'success');
  assert.equal(result.reply, 'ok');
});

test('FastApiClient returns the fallback reply after repeated timeouts', async () => {
  const client = new FastApiClient();
  client.client.post = async () => {
    throw { code: 'ECONNABORTED', message: 'timeout' };
  };

  const result = await client.forward({ message: 'hello' });

  assert.equal(result.status, 'error');
  assert.equal(result.reply, client.fallbackReply);
});

test('WhatsAppBridge builds the FastAPI payload with required schema and timestamp', () => {
  const bridge = new WhatsAppBridge();
  const normalized = {
    chat_id: '919999999999@c.us',
    message: 'Hello Nezuko',
  };

  const payload = bridge.buildFastApiPayload(normalized);

  assert.equal(payload.platform_id, 'whatsapp');
  assert.equal(payload.chat_id, '919999999999@c.us');
  assert.equal(payload.message, 'Hello Nezuko');
  assert.equal(typeof payload.timestamp, 'number');
  assert.ok(payload.timestamp > 0);
});

test('WhatsAppBridge sends the FastAPI reply back to WhatsApp users', async () => {
  const bridge = new WhatsAppBridge();
  const normalized = {
    phone_number: '919999999999',
    chat_id: '919999999999@c.us',
    message: 'Hello Nezuko',
  };

  let sentReply = null;
  bridge.fastApi.forward = async () => ({ status: 'success', reply: 'Hiiiii there! 👋' });
  bridge.sendReply = async (to, text) => {
    sentReply = { to, text };
  };

  await bridge.processMessage({ normalized, dedupeKey: 'test-key' });

  assert.deepEqual(sentReply, { to: '919999999999', text: 'Hiiiii there! 👋' });
});

test('WhatsAppBridge verifies incoming webhook tokens correctly', () => {
  const bridge = new WhatsAppBridge();
  bridge.verifyToken = 'secret';

  const result = bridge.verifyWebhook({
    'hub.mode': 'subscribe',
    'hub.verify_token': 'secret',
    'hub.challenge': 'challenge-code',
  });

  assert.equal(result.ok, true);
  assert.equal(result.challenge, 'challenge-code');
});

test('BaileysClient forwards valid messages to FastAPI and sends replies over Baileys', async () => {
  const client = new BaileysClient();
  const sentMessages = [];
  let forwardedPayload = null;

  client.sock = {
    sendMessage: async (to, message) => {
      sentMessages.push({ to, message });
      return { key: { id: 'outbound-1' } };
    },
  };
  client.ready = true;
  client.fastApi.forward = async (payload) => {
    forwardedPayload = payload;
    return { status: 'success', reply: 'Hello from FastAPI' };
  };
  client.sendText = async (to, text) => {
    sentMessages.push({ to, text });
    return { key: { id: 'outbound-2' } };
  };

  const result = await client.handleIncomingWebhook({
    chat_id: '919999999999@c.us',
    message: 'Nezuko hello',
    phone_number: '919999999999',
    timestamp: 1710000000,
    raw_message_id: 'msg-123',
  });

  assert.equal(result.status, 'success');
  assert.equal(result.reply, 'Hello from FastAPI');
  assert.equal(forwardedPayload.platform_id, 'whatsapp');
  assert.equal(forwardedPayload.chat_id, '919999999999@c.us');
  assert.equal(forwardedPayload.message, 'Nezuko hello');
  assert.equal(typeof forwardedPayload.timestamp, 'number');
  assert.equal(sentMessages[sentMessages.length - 1].to, '919999999999@c.us');
  assert.equal(sentMessages[sentMessages.length - 1].text, 'Hello from FastAPI');
});
