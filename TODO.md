# TODO - Production readiness fixes

- [x] HEALTH CHECK: Update root endpoint to support both GET and HEAD with 200 response.
- [x] GEMINI MIGRATION: Replace `google.generativeai` with `google-genai` while preserving prompts, generation_config, model name, behavior, and error handling.
- [x] GEMINI PERFORMANCE: Remove heavy Gemini initialization at import time; lazy-init on first AI request.
- [x] STARTUP/SHUTDOWN: Wire APScheduler start/stop into FastAPI lifespan; ensure graceful shutdown ordering vs Mongo close.

- [x] LOGGING: Improve logging separation; add request logging middleware with suppression for HEAD `/`; prevent `basicConfig` duplication.

- [x] PERFORMANCE: Verify startup time is reduced (no Gemini model/listing at startup).

- [x] TESTING: Manual checks after deploy: `HEAD /` -> 200, sample AI routes, scheduler shutdown behavior.


