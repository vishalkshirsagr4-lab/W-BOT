# Backend service

This folder contains the FastAPI backend for the Nezuko assistant.

## What changed
- Added a dedicated Nezuko command and memory service for wake-word routing, command handling, and conversation persistence.
- The backend now stores chat history in MongoDB with configurable expiry-based pruning.
- Conversation retention defaults to 200 messages per chat and 12,000 characters per message. Configure `CONVERSATION_TTL_SECONDS`, `CONVERSATION_MAX_MESSAGES`, and `CONVERSATION_MAX_MESSAGE_CHARS` in `.env`.
- Study-note records store resource links and metadata; the backend does not store PDF/video binaries inside MongoDB.
- Added health endpoints and stronger startup/shutdown logging.

## Run locally

From the repository root:

```bash
uvicorn backend.main:app --reload
```

## Useful endpoints
- GET /health
- POST /api/v1/chat/ask
- POST /api/v1/whatsapp/message
