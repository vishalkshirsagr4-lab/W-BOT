# Project TODO (WhatsApp AI Bot)

## Phase 1 — Fix async warning (Problem 1)
- [x] Convert `_gemini_send_message_blocking` to synchronous `def` so `asyncio.to_thread(...)` never receives an async function.
- [x] Ensure the Gemini call path produces no `coroutine was never awaited` warnings.

## Phase 2 — Gemini init at startup (Problem 2)
- [x] Gemini initialization moved to FastAPI startup (one-time).

## Phase 3 — WhatsApp endpoint latency (2–5s goal)
- [ ] Add stage timing logs (validation, Mongo reads/writes, Gemini start/finish, formatting, total).
- [ ] Reduce sequential Mongo roundtrips in `routes/whatsapp.py`.
- [ ] Ensure no blocking I/O on async path.

## Phase 4 — Backend HTTP client reuse (secondary)
- [ ] Reuse a single `httpx.AsyncClient` for `routes/utils.py` and close it on shutdown.

## Phase 5 — Verification
- [ ] Repo-wide search for `was never awaited` and misuse of `to_thread` with async functions.
- [ ] Smoke test WhatsApp `/message` and confirm response under 5 seconds in practice.

