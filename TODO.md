# TODO - FastAPI latency root-cause + async/perf fixes

- [x] Identify root cause of 60–120s latency using code review
- [x] Fix Gemini/AI pipeline: enforce hard timeout (5–10s) + event-loop safety (run blocking SDK call off-thread)
- [x] Remove/limit long retry delays after global timeout budget exceeded

- [x] Reduce hot-path logging inside `routes/whatsapp.py`

- [ ] Provide updated production-ready code: `ai/chat.py` and `routes/whatsapp.py`
- [ ] Smoke test locally: `python -m compileall .` and a quick uvicorn request

