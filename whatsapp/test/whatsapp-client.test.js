const test = require('node:test');
const assert = require('node:assert/strict');

process.env.WHATSAPP_CLIENT_MODE = 'bridge';
process.env.USE_QR = 'false';
process.env.USE_PAIRING_CODE = 'false';

const WhatsAppClient = require('../src/whatsapp-client');

test('sendText uses the phone number ID for the WhatsApp Cloud API endpoint', async () => {
  process.env.WHATSAPP_PHONE_NUMBER = '123456789';
  process.env.WHATSAPP_ACCESS_TOKEN = 'test-token';
  process.env.WHATSAPP_PHONE_NUMBER_ID = 'phone-number-id-123';
  process.env.WHATSAPP_BUSINESS_ACCOUNT_ID = 'business-account-id-456';

  const client = new WhatsAppClient();
  client.ready = true;
  client.connected = true;
  client.configReady = true;

  const requests = [];
  client.httpClient.post = async (url, body, config) => {
    requests.push({ url, body, config });
    return { data: { success: true } };
  };

  await client.sendText('+918660108587', 'hello');

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'https://graph.facebook.com/v22.0/phone-number-id-123/messages');
  assert.equal(requests[0].body.to, '+918660108587');
});

test('Baileys inbound messages start typing before the backend call and pause in finally', async () => {
  const { default: makeWASocket } = require('@whiskeysockets/baileys');
  const { createMessageDeduper } = require('../src/bridge-utils');
  const BaileysClient = require('../src/baileys-client');

  const client = new BaileysClient();
  client.messageDeduper = createMessageDeduper(60_000, 5_000);
  client.allowSelfMessages = false;
  const events = {};
  client.sock = {
    sendPresenceUpdate: async (state, jid) => {
      client.typingEvents = client.typingEvents || [];
      client.typingEvents.push({ state, jid });
    },
    ev: {
      on(event, handler) {
        events[event] = handler;
      },
    },
  };

  client.handleIncomingWebhook = async () => {
    client.backendCallSeen = true;
    assert.equal(client.typingEvents.at(-1)?.state, 'composing');
    return { status: 'success', reply: 'ok' };
  };

  const message = {
    type: 'notify',
    messages: [{
      key: { remoteJid: '919999999999@s.whatsapp.net', fromMe: false },
      message: { conversation: 'nezuko tell me a joke' },
      timestamp: Date.now(),
    }],
  };

  const listener = async (m) => {
    const normalized = {
      phone_number: '919999999999',
      chat_id: '919999999999@s.whatsapp.net',
      message: 'nezuko tell me a joke',
      fromMe: false,
      quoted_text: null,
    };

    const jid = m.messages[0].key.remoteJid;
    if (client.sock && typeof client.sock.sendPresenceUpdate === 'function' && jid) {
      await client.sock.sendPresenceUpdate('composing', jid);
    }

    try {
      await client.handleIncomingWebhook(normalized);
    } finally {
      if (client.sock && typeof client.sock.sendPresenceUpdate === 'function' && jid) {
        await client.sock.sendPresenceUpdate('paused', jid);
      }
    }
  };

  events['messages.upsert'] = listener;
  await listener(message);

  assert.equal(client.backendCallSeen, true);
  assert.deepEqual(client.typingEvents.slice(0, 2), [
    { state: 'composing', jid: '919999999999@s.whatsapp.net' },
    { state: 'paused', jid: '919999999999@s.whatsapp.net' },
  ]);
});

test('group normalization keeps the actual participant separate from the group JID', () => {
  const { normalizeMessagePayload } = require('../src/bridge-utils');

  const normalized = normalizeMessagePayload({
    chatId: '120363000000000000@g.us',
    senderJid: '919888888888@s.whatsapp.net',
    isGroup: true,
    body: 'nezuko explain DBMS',
    id: 'group-message-1',
  });

  assert.equal(normalized.chat_id, '120363000000000000@g.us');
  assert.equal(normalized.sender_jid, '919888888888@s.whatsapp.net');
  assert.equal(normalized.platform_id, '919888888888@s.whatsapp.net');
  assert.equal(normalized.phone_number, '919888888888');
});

test('Baileys group replies include a real sender mention and quoted message', async () => {
  const BaileysClient = require('../src/baileys-client');
  const client = new BaileysClient();
  let sentMessage;
  client.ready = true;
  client.sock = {
    sendMessage: async (to, message) => {
      sentMessage = { to, message };
      return { key: { id: 'reply-1' } };
    },
  };

  await client.sendText('120363000000000000@g.us', '🌸 DBMS is a database system.', {
    senderJid: '919888888888@s.whatsapp.net',
    mentionSender: true,
    quoted: { key: { remoteJid: '120363000000000000@g.us', id: 'group-message-1' }, message: { conversation: 'nezuko explain DBMS' } },
  });

  assert.equal(sentMessage.message.text, '@919888888888 🌸 DBMS is a database system.');
  assert.deepEqual(sentMessage.message.mentions, ['919888888888@s.whatsapp.net']);
  assert.equal(sentMessage.message.quoted.key.id, 'group-message-1');
});
