const fs = require('fs');
const path = require('path');
const makeWASocket = require('@whiskeysockets/baileys').default;
const { DisconnectReason, useMultiFileAuthState } = require('@whiskeysockets/baileys');
const { getEnv, getIntEnv, getBoolEnv } = require('./config');
const logger = require('./logger');
const { generateQrDataUrl, generateQrPngBuffer, generateQrSvgBuffer, printQrToTerminal } = require('./qr-utils');
const { createMessageDeduper, normalizeMessagePayload, shouldProcessMessage, isGlobalCommand } = require('./bridge-utils');
const FastApiClient = require('./fastapi');

class BaileysClient {
  constructor() {
    this.sock = null;
    this.ready = false;
    this.authenticated = false;
    this.qrCode = '';
    this.qrDataUrl = '';
    this.qrPngBuffer = null;
    this.qrSvgBuffer = null;
    this.status = 'stopped';
    this.lastSeen = null;
    this.startPromise = null;
    this.sessionPath = path.resolve(getEnv('WHATSAPP_SESSION_PATH', './.baileys_auth'));
    this.reconnectDelayMs = getIntEnv('WHATSAPP_RECONNECT_DELAY_MS', 5000);
    this.headless = getBoolEnv('WHATSAPP_HEADLESS', true);
    this.shutdownRequested = false;
    this.authState = null;
    this.authTimeoutMs = getIntEnv('WHATSAPP_AUTH_TIMEOUT_MS', 300000);
    this.authTimer = null;
    this.authTimerStartedAt = null;
    this.authExpired = false;
    this.pendingQr = false;
    this.reconnectTimer = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = getIntEnv('WHATSAPP_MAX_RECONNECT_ATTEMPTS', 8);
    this.fastApi = new FastApiClient();
    this.allowSelfMessages = getBoolEnv('ALLOW_SELF_MESSAGES', false);
    this.apiTimeoutMs = getIntEnv('FASTAPI_TIMEOUT_MS', 8000);
    this.messageDeduper = createMessageDeduper(getIntEnv('MESSAGE_DEDUP_TTL_MS', 60_000), getIntEnv('MESSAGE_DEDUP_MAX_ENTRIES', 5_000));
  }

  async ensureSessionDir() {
    fs.mkdirSync(this.sessionPath, { recursive: true });
  }

  clearAuthTimer() {
    if (this.authTimer) {
      clearTimeout(this.authTimer);
      this.authTimer = null;
    }
    this.authTimerStartedAt = null;
  }

  startAuthTimer() {
    if (this.authExpired || this.authenticated || this.ready) {
      return;
    }

    this.clearAuthTimer();
    this.authTimerStartedAt = Date.now();
    this.authTimer = setTimeout(() => {
      this.authExpired = true;
      logger.warn('Authentication timeout after 5 minutes.');
      this.stopForAuthTimeout().catch((error) => logger.error({ err: error?.message || error }, 'Authentication timeout cleanup failed'));
    }, this.authTimeoutMs);
  }

  async stopForAuthTimeout() {
    this.status = 'auth_timeout';
    this.pendingQr = false;
    this.qrCode = '';
    this.qrDataUrl = '';
    this.qrPngBuffer = null;
    this.qrSvgBuffer = null;
    this.clearAuthTimer();

    if (this.sock) {
      try {
        await this.sock.logout?.();
      } catch (error) {
        logger.warn({ err: error?.message || error }, 'Logout during auth timeout failed');
      }
      try {
        await this.sock.ws?.close();
      } catch (error) {
        logger.warn({ err: error?.message || error }, 'Socket close during auth timeout failed');
      }
    }

    this.sock = null;
    this.ready = false;
    this.authenticated = false;
  }

  async start() {
    if (this.sock && this.ready) {
      return this.sock;
    }

    if (this.startPromise) {
      return this.startPromise;
    }

    this.startPromise = this.initialize();
    try {
      return await this.startPromise;
    } finally {
      this.startPromise = null;
    }
  }

  clearReconnectTimer() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  async initialize() {
    this.status = 'starting';
    this.shutdownRequested = false;
    await this.ensureSessionDir();

    logger.info({ sessionPath: this.sessionPath, headless: this.headless }, 'Starting Baileys client');

    const { state, saveCreds } = await useMultiFileAuthState(path.join(this.sessionPath, 'session'));
    this.authState = state;

    this.sock = makeWASocket({
      auth: state,
      printQRInTerminal: false,
      browser: ['W-BOT', 'Chrome', '1.0.0'],
      syncFullHistory: false,
    });

    this.sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        if (this.authExpired || this.authenticated || this.ready) {
          return;
        }

        this.pendingQr = true;
        this.qrCode = qr;
        this.qrDataUrl = await generateQrDataUrl(qr);
        this.qrPngBuffer = await generateQrPngBuffer(qr);
        this.qrSvgBuffer = await generateQrSvgBuffer(qr);
        this.status = 'qr_required';
        this.ready = false;
        this.authenticated = false;
        printQrToTerminal(qr);
        this.startAuthTimer();
        logger.info({ qrLength: qr.length }, 'QR generated');
      }

      if (connection === 'open') {
        this.clearAuthTimer();
        this.pendingQr = false;
        this.authExpired = false;
        this.ready = true;
        this.authenticated = true;
        this.status = 'ready';
        this.lastSeen = new Date().toISOString();
        logger.info('Authentication successful');
      }

      if (connection === 'close') {
        this.ready = false;
        this.authenticated = false;
        this.status = 'disconnected';
        const disconnectCode = lastDisconnect?.error?.output?.statusCode;
        const recoverable = disconnectCode !== DisconnectReason.loggedOut && disconnectCode !== DisconnectReason.connectionReplaced;
        logger.warn({ reason: lastDisconnect?.error?.message || 'connection closed', recoverable }, 'Reconnecting');

        if (!this.shutdownRequested && recoverable && this.reconnectAttempts < this.maxReconnectAttempts) {
          this.reconnectAttempts += 1;
          this.clearReconnectTimer();
          this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this.restart().catch((error) => logger.error({ err: error?.message || error }, 'Baileys reconnect failed'));
          }, this.reconnectDelayMs * this.reconnectAttempts);
        } else if (disconnectCode === DisconnectReason.loggedOut) {
          logger.info('Logged out');
        }
      }
    });

    this.sock.ev.on('creds.update', saveCreds);

    this.sock.ev.on('connection.update', async (update) => {
      const { connection, qr } = update;
      if (connection === 'open' && qr) {
        logger.info('QR scanned');
      }
    });

    this.sock.ev.on('messages.upsert', async (m) => {
      if (m.type !== 'notify') {
        return;
      }

      const message = m.messages?.[0];
      if (!message) {
        return;
      }

      const normalized = normalizeMessagePayload({
        from: message.key?.remoteJid,
        chatId: message.key?.remoteJid,
        body: message.message?.conversation || message.message?.extendedTextMessage?.text || '',
        type: 'chat',
        timestamp: Date.now() / 1000,
        fromMe: Boolean(message.key?.fromMe),
        isStatus: false,
        isBroadcast: false,
        hasMedia: Boolean(message.message?.imageMessage || message.message?.videoMessage || message.message?.documentMessage || message.message?.audioMessage || message.message?.stickerMessage),
        quotedMsg: message.message?.extendedTextMessage?.contextInfo?.quotedMessage ? {
          body: message.message?.extendedTextMessage?.contextInfo?.quotedMessage?.conversation || '',
        } : undefined,
      });

      if (normalized) {
        normalized.fromMe = Boolean(message.key?.fromMe);
        normalized.isStatus = false;
        normalized.isBroadcast = false;
        normalized.isOwnMessage = normalized.fromMe;
      }

      if (!normalized) {
        logger.warn({ remoteJid: message.key?.remoteJid }, 'Baileys message payload was invalid');
        return;
      }

      logger.info(
        {
          from: normalized.phone_number,
          chatId: normalized.chat_id,
          messageText: normalized.message,
          fromMe: message.key?.fromMe,
        },
        'Incoming Baileys message received'
      );

      const isRecognizedGlobalCommand = isGlobalCommand(normalized.message);
      if (isRecognizedGlobalCommand) {
        logger.info({ chatId: normalized.chat_id, messageText: normalized.message }, 'Recognized global command');
      }

      if (!shouldProcessMessage(normalized, { allowSelfMessages: this.allowSelfMessages })) {
        logger.info({ from: normalized.phone_number, chatId: normalized.chat_id, messageText: normalized.message }, 'Baileys message ignored by bridge filter');
        return;
      }

      // Special commands should bypass the group mention requirement.
      const _msgText = String(normalized.message || '').trim();
      const _lowerMsg = _msgText.toLowerCase();
      if (/^(\/dw|\/tts|\/vc|\/voice)(\s|$)/i.test(_msgText)) {
        // Minimal DW logging; avoid logging full message text to reduce sensitive data exposure
        logger.info({ chatId: normalized.chat_id }, '[DW] Download command detected');
        const _url = _msgText.length > 3 ? _msgText.slice(3).trim() : '';
        if (_lowerMsg.startsWith('/dw ') && _url) {
          logger.info({ chatId: normalized.chat_id, url: _url }, '[DW] URL detected');
        }
        try {
          const response = await this.handleIncomingWebhook?.(normalized);
          logger.info({ chatId: normalized.chat_id, responseStatus: response?.status }, '[DW] forwarded to backend');
        } catch (error) {
          logger.error({ err: error?.message || error, chatId: normalized.chat_id }, '[DW] forwarding failed');
        }
        return;
      }

      if (!isRecognizedGlobalCommand && !/nezuko/i.test(normalized.message || '') && !/nezuko/i.test(String(normalized.quoted_text || ''))) {
        logger.info({ from: normalized.phone_number, chatId: normalized.chat_id, messageText: normalized.message }, 'Baileys message ignored because it did not mention Nezuko');
        return;
      }

      if (isRecognizedGlobalCommand) {
        logger.info({ chatId: normalized.chat_id, messageText: normalized.message }, 'Forwarding command to bridge handler');
      }

      const dedupeKey = normalized.raw_message_id || `${normalized.chat_id}:${normalized.timestamp}`;
      if (!this.messageDeduper.shouldProcess(dedupeKey)) {
        logger.info({ from: normalized.phone_number, chatId: normalized.chat_id, dedupeKey }, 'Baileys message ignored as duplicate');
        return;
      }

      const jid = message.key?.remoteJid;
      const typingState = jid || normalized.chat_id;

      if (this.sock && typeof this.sock.sendPresenceUpdate === 'function' && typingState) {
        try {
          await this.sock.sendPresenceUpdate('composing', typingState);
          logger.info({ jid: typingState }, 'Baileys typing indicator started');
        } catch (error) {
          logger.warn({ err: error?.message || error, jid: typingState }, 'Baileys typing indicator start failed');
        }
      }

      try {
        const response = await this.handleIncomingWebhook?.(normalized);
        logger.info({ from: normalized.phone_number, chatId: normalized.chat_id, status: response?.status }, 'Baileys message forwarded to bridge handler');
      } catch (error) {
        logger.error({ err: error?.message || error, from: normalized.phone_number, chatId: normalized.chat_id }, 'Baileys message forwarding failed');
      } finally {
        if (this.sock && typeof this.sock.sendPresenceUpdate === 'function' && typingState) {
          try {
            await this.sock.sendPresenceUpdate('paused', typingState);
            logger.info({ jid: typingState }, 'Baileys typing indicator stopped');
          } catch (error) {
            logger.warn({ err: error?.message || error, jid: typingState }, 'Baileys typing indicator stop failed');
          }
        }
      }
    });

    this.status = 'connecting';
    return this.sock;
  }

  async restart() {
    this.clearReconnectTimer();
    if (this.sock) {
      try {
        await this.sock.logout?.();
      } catch (error) {
        logger.warn({ err: error?.message || error }, 'Baileys logout failed');
      }
    }
    this.sock = null;
    this.ready = false;
    this.authenticated = false;
    this.status = 'restarting';
    await this.start();
  }

  async stop() {
    this.shutdownRequested = true;
    this.clearReconnectTimer();
    this.clearAuthTimer();
    this.status = 'stopping';
    if (this.sock) {
      try {
        await this.sock.ws?.close();
      } catch (error) {
        logger.warn({ err: error?.message || error }, 'Baileys shutdown failed');
      }
    }
    this.sock = null;
    this.ready = false;
    this.authenticated = false;
    this.status = 'stopped';
  }

  getHealthSnapshot() {
    return {
      ready: this.ready,
      authenticated: this.authenticated,
      status: this.status,
      qrAvailable: Boolean(this.qrDataUrl),
      qrCode: this.qrCode,
      lastSeen: this.lastSeen,
      sessionPath: this.sessionPath,
      authExpired: this.authExpired,
      authTimerStartedAt: this.authTimerStartedAt,
      reconnectAttempts: this.reconnectAttempts,
    };
  }

  verifyWebhook(query = {}) {
    return { ok: false, challenge: null };
  }

  async handleIncomingWebhook(payload) {
    const normalized = payload?.chat_id || payload?.chatId || payload?.from
      ? payload
      : normalizeMessagePayload(payload);

    if (!normalized || !normalized.chat_id || !normalized.message) {
      logger.warn({ payloadReceived: Boolean(payload) }, 'Baileys webhook payload was invalid; dropping message');
      return { status: 'ignored', reason: 'invalid_payload' };
    }

    const forwardPayload = {
      platform_id: 'whatsapp',
      chat_id: normalized.chat_id,
      message: normalized.message,
      timestamp: Math.floor(Date.now() / 1000),
    };

    logger.info({ chatId: normalized.chat_id, messageText: normalized.message }, 'Forwarding Baileys inbound message to FastAPI');

    try {
      const response = await this.fastApi.forward(forwardPayload, { timeoutMs: this.apiTimeoutMs });
      logger.info({ chatId: normalized.chat_id, responseStatus: response?.status }, 'Received FastAPI response for Baileys inbound message');

      if (response?.status === 'success' && typeof response.reply === 'string' && response.reply.trim()) {
        const replyText = response.reply.trim();
        await this.sendText(normalized.chat_id, replyText);
        return { status: 'success', reply: replyText };
      }

      logger.warn({ chatId: normalized.chat_id, reason: response?.reason || 'no_reply' }, 'FastAPI did not return a usable reply for Baileys message');
      return { status: 'ignored', reason: response?.reason || 'no_reply' };
    } catch (error) {
      logger.error({ err: error?.message || error, chatId: normalized.chat_id }, 'FastAPI forwarding failed for Baileys inbound message');
      return { status: 'error', reason: 'fastapi_forward_failed' };
    }
  }

  async sendText(to, text) {
    if (!this.sock || !this.ready) {
      throw new Error('Baileys client is not ready');
    }
    logger.info({ to, messageLength: text.length }, 'Sending outbound Baileys text');
    const result = await this.sock.sendMessage(to, { text });
    logger.info({ to, messageLength: text.length, messageId: result?.key?.id || null }, 'Outbound Baileys text sent successfully');
    return result;
  }

  async sendMedia(to, mediaUrl, opts = {}) {
    if (!this.sock || !this.ready) {
      throw new Error('Baileys client is not ready');
    }
    const mediaType = (opts.mediaType || 'video').toLowerCase();
    const caption = opts.caption || '';
    logger.info({ to, mediaUrl, mediaType }, 'Sending outbound Baileys media');
    const message = {};
    if (mediaType === 'video') {
      message.video = { url: mediaUrl };
      if (caption) message.caption = caption;
    } else if (mediaType === 'audio') {
      message.audio = { url: mediaUrl };
      message.mimetype = 'audio/mpeg';
      message.ptt = false;
    } else if (mediaType === 'image' || mediaType === 'photo') {
      message.image = { url: mediaUrl };
      if (caption) message.caption = caption;
    } else {
      // fallback to document
      message.document = { url: mediaUrl, mimetype: 'application/octet-stream', fileName: opts.filename || 'file' };
      if (caption) message.caption = caption;
    }

    const result = await this.sock.sendMessage(to, message);
    logger.info({ to, mediaType, messageId: result?.key?.id || null }, 'Outbound Baileys media sent successfully');
    return result;
  }
}

module.exports = BaileysClient;
