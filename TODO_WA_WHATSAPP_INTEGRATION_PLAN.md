# WhatsApp Integration Plan (whatsapp-web.js -> existing FastAPI)

## Information gathered
- FastAPI backend root files/folders: `main.py`, `routes/*`, `ai/chat.py`, `database/connection.py`, `models/*`.
- Existing AI function: `ai/chat.py::generate_chat_response(user_message, chat_history=...)` uses Gemini (Gemini key from `config/settings.py`).
- Existing chat route: `routes/chat.py` exposes `POST /api/v1/chat/ask` and returns `{ status, reply }`.
- FastAPI app mounts routers under `/api/v1`.
- There is currently no WhatsApp ingest endpoint.

## Plan
### Goal A — Add minimal WhatsApp ingest API to reuse existing AI
1. Create `routes/whatsapp.py`.
2. Add endpoint `POST /api/v1/whatsapp/message`:
   - Validate payload fields.
   - Enforce access control via Mongo collections:
     - `allowed_users`, `allowed_groups`, `blocked_users`, `chat_settings`.
   - Evaluate reply mode:
     - Private: AI ON/OFF, Commands Only, Silent.
     - Group: AI ON/OFF, Mention Only, Prefix Mode (`/ai`, `!ai`, `#ai`), Reply Only, Always.
   - Call `generate_chat_response()` for AI replies.
   - Return `{ status: "success", reply: "..." }`.

### Goal B — Add whatsapp-web.js Node.js bot
3. Create `whatsapp/` directory with:
   - `package.json`
   - `index.js`
   - `config/`, `utils/`, `services/` as needed (one file at a time).
4. Implement runtime:
   - `LocalAuth` with `SESSION_NAME`.
   - QR displayed only on first auth/session recovery.
   - Auto reconnect.
   - Normalize WhatsApp messages and POST to FastAPI endpoint.
   - On response, send message back to WhatsApp.
   - Owner-only commands handled locally in Node.js; for admin actions, call existing FastAPI admin routes if applicable.

### Goal C — Production hardening
5. Add:
   - HTTP timeouts + retries.
   - Rate limiting per sender (Node.js).
   - Structured logs.
   - Safe handling for unsupported message types.

## Dependent files to edit/create
- Create: `routes/whatsapp.py`
- Edit: `main.py` to mount the new router under `/api/v1`
- Create: `whatsapp/package.json`, `whatsapp/index.js` (then helpers)

## Followup steps
- Install Node deps and run bot.
- Run FastAPI and verify endpoint with a simple curl/postman test.

## <ask_followup_question>
Confirm the access-control enforcement location:
- Option 1 (recommended): FastAPI fully enforces access control; Node.js forwards all candidate messages.
- Option 2: Some access control in Node.js too.

Reply with `Option 1` or `Option 2`.

