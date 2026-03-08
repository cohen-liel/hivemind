# Improvement Plan — Web Claude Bot

**Last updated**: 2026-03-08 (Round 3 — all critical/high/medium items done)

---

## STATUS SUMMARY

| # | ID | Description | Status |
|---|-----|------------|--------|
| 1 | C7 | `session_manager.py` — duplicate `close()` deleted | ✅ DONE |
| 2 | C3 | `sdk_client.py` — hardcoded auth token → env var | ✅ DONE |
| 3 | H7 | `orchestrator.py` — `results={}` moved before loop | ✅ DONE |
| 4 | H8 | `state.py` — dict snapshots in iterations | ✅ DONE |
| 5 | H11 | `server.py` — `validate_config()` called at startup | ✅ DONE |
| 6 | H10 | `server.py` — await cancelled tasks + close DB | ✅ DONE |
| 7 | M11 | `api.py` — `__import__('time')` anti-pattern removed | ✅ DONE |
| 8 | C4 | `api.py` — `browse_dirs` restricted to home dir | ✅ DONE |
| 9 | C5 | `api.py` — `persist_settings` key whitelist added | ✅ DONE |
| 10 | H5 | `api.py` — `CreateScheduleRequest` Pydantic + HH:MM validator | ✅ DONE |
| 11 | H2 | `orchestrator.py` — `list(conversation_log)` snapshot | ✅ DONE |
| 12 | H12 | `events.py` — async lock in subscribe/unsubscribe/publish | ✅ DONE |
| 13 | H3/H4 | `session_manager.py` — proper methods, raw SQL removed from api.py | ✅ DONE |
| 14 | M1 | `api.py` — optional `DASHBOARD_API_KEY` middleware | ✅ DONE |
| 15 | M3 | `orchestrator.py` + `api.py` — all `subprocess.run()` → async | ✅ DONE |
| 16 | M6 | `orchestrator.py` — log capped at 2000, `_agents_used` set | ✅ DONE |
| 17 | M7 | Dockerfile + docker-compose + .dockerignore improved | ✅ DONE |
| 18 | M9 | `requirements.txt` — all packages pinned with `==` versions | ✅ DONE |
| 19 | A1 | Extract services layer (api.py → services/) | 🔲 Future sprint |
| 20 | A3 | Pydantic response models on all endpoints | 🔲 Future sprint |
| 21 | F5 | Enhanced health check (DB, CLI, disk) | ✅ DONE |
| 22 | F6 | Rate limiting middleware (per-IP) | 🔲 Future sprint |

---

## Remaining for Future Sprint (A1, A3, F6)

### A1 — Extract services layer
`api.py` mixes routing, business logic, git commands, and dict formatting.
Extract to: `services/project_service.py`, `services/agent_service.py`.
**Effort**: 2-3h | **Value**: testability, maintainability

### A3 — Pydantic response models
All endpoints return raw `dict`. Add typed response models for OpenAPI docs + validation.
**Effort**: 2h | **Value**: auto-docs, type safety

### F6 — Rate limiting middleware
Protect all `/api/` endpoints from abuse (per-IP token bucket).
Can use `slowapi` or a simple in-memory counter.
**Effort**: 1h | **Value**: production safety

---

## Additional Improvements Made This Session (not in original plan)

| Fix | Description |
|-----|-------------|
| STANDBY agents | delegation → `state='working'` immediately; `done`/`error` preserved after task |
| Page refresh survival | `_send_final()` persists to SQLite before WS publish |
| Poll failsafe | 10s poll merges server `agent_states` to recover missed WS events |
| Smart skill selection | top-5 skills by keyword score (~90% token reduction per call) |
| Rate limiting | 3s gap between orchestrator LLM calls |
| conversation_log cap | 2000 entries; `_agents_used` set for O(1) agent tracking |
| Budget warning | progressive alert at 80% threshold |
| Config overrides | all settings use `_get()` for `settings_overrides.json` |
| TASK_COMPLETE gate | rejected completions now run accompanying delegate blocks |
| File conflict label | `_conflicts` → `⚠ File Conflicts` readable key |
| Stuck detector | scans full log (not just last 8 entries) |
| Session context reset | `shared_context` + `_agents_used` reset between sessions |
| EPIC continuations | manifest presence no longer downgrades EPIC → LARGE |
| Unknown role feedback | invalid agent name injects `⚠ Invalid Role` error + valid roles list |
| inject_user_message | routes to actual target agent; increments `turn_count` |
| Orchestrator skills | skill content now injected into orchestrator system prompt |
| CLI error message | replaced ANTHROPIC_API_KEY reference with `claude login` guidance |
