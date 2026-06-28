# Nezuko Upgrade TODO (incremental, backward compatible)

## Step 0: Compliance audit results
- [x] Reviewed current implementation: WhatsApp trigger + auto upsert exist in `routes/whatsapp.py`
- [x] Reviewed AI: Gemini persona in `ai/chat.py` without memory
- [x] Reviewed admin: partial admin endpoints, not full RBAC
- [x] Reviewed security: basic bridge rate limit only

## Step 1: Clean Architecture scaffolding (backward compatible)
- [ ] Create `app/` folder structure: api, routes, services, repositories, models, schemas, core, utils, database, config, middleware, permissions, ai
- [ ] Introduce app-level payload/schema for WhatsApp message
- [ ] Create repositories for Mongo collections used by WhatsApp flow (users/groups/chat_settings/blocked_users)
- [ ] Create services:
  - [ ] WhatsAppMessageService (orchestrator)
  - [ ] TriggerService (Nezuko trigger matrix)
  - [ ] UserRegistrationService (auto upsert)
  - [ ] GroupRegistrationService (auto upsert)
- [ ] Extract current logic from `routes/whatsapp.py` into services
- [ ] Keep `/api/v1/whatsapp/message` endpoint behavior unchanged

## Step 2: Nezuko trigger matrix parity
- [ ] Ensure case-insensitive matching for all listed trigger forms
- [ ] Verify private chat reply modes: Always Reply / Name Trigger / Commands Only / Silent / Disabled
- [ ] Verify group chat reply modes: Always / Mention Only / Name Trigger / Commands Only / Reply Only / Disabled / Custom Prefix

## Step 3: RBAC foundation
- [ ] Add collections: roles, permissions, admins (or user_roles)
- [ ] Implement permission dependency
- [ ] Add admin role mapping shim for backward compatibility with `is_admin`

## Step 4: Admin APIs
- [ ] Implement required endpoints (enable/disable AI, broadcast, stats, reset memory, export/import, block/mute/archive/restore, delete, promote/demote, system config, reload, health)

## Step 5: Memory system
- [ ] conversation_history sliding window
- [ ] memory extraction + summarization
- [ ] context builder for Gemini

## Step 6: File intelligence & college assistant
- [ ] document upload + extraction (PDF/CSV/XLSX/TXT/DOCX)
- [ ] timetable/assignment/exam/subject lookup
- [ ] notes search + academic Q&A

## Step 7: Security hardening
- [ ] request authentication from Node bridge
- [ ] distributed rate limiting
- [ ] duplicate detection + replay protection
- [ ] loop prevention

## Step 8: Observability & performance
- [ ] structured logging
- [ ] latency logging (AI + Mongo)
- [ ] indexes + caching

