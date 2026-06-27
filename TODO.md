# TODO — College Community Bot

## Step 1: Repo diagnosis (already partially done)
- [x] Read core files: main.py, config/settings.py, database/connection.py, models/*, routes/*, ai/chat.py, scheduler/tasks.py
- [ ] Verify missing/hidden imports and identify any non-existent modules

## Step 2: Fix broken connections
- [ ] Replace all `app.*` imports with correct relative imports for current folder layout
- [ ] Fix `main.py` router registration (remove duplicate include_router, correct imports)
- [ ] Ensure routers/study/games/admin/chat/utils are all wired

## Step 3: Produce comprehensive brainstorm_plan (must-have)
- [ ] Generate plan covering: FastAPI API, MongoDB schema, JWT/security (if used), AI endpoints, study, economy, admin, scheduler automation, community features, games, utilities

## Step 4: Implementation (generate files one at a time)
- [ ] Create app structure only within current root layout (no `app/` package), while keeping modular folders
- [ ] Add missing models/schemas and endpoints to cover all commands listed
- [ ] Add rate limiting/spam protection/logging
- [ ] Add tests, dockerization, and deployment docs

## Step 5: Production deliverables
- [ ] requirements.txt (complete)
- [ ] Dockerfile
- [ ] README.md
- [ ] .env.example
- [ ] MongoDB schema/indexes documentation
- [ ] API docs (OpenAPI / route descriptions)
- [ ] Testing guide

