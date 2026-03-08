# Improvement Plan — Web Claude Bot

**Last updated**: 2026-03-08 — All items complete except future-sprint refactors.

---

## Final Status — All 22 Items

| # | ID | Description | Status |
|---|-----|------------|--------|
| 1 | C7 | `session_manager.py` — delete duplicate `close()` | ✅ DONE |
| 2 | C3 | `sdk_client.py` — remove hardcoded token + entire Meta proxy block | ✅ DONE |
| 3 | H7 | `orchestrator.py` — `results={}` moved before loop | ✅ DONE |
| 4 | H8 | `state.py` — dict snapshots in all iterations | ✅ DONE |
| 5 | H11 | `server.py` — `validate_config()` called at startup | ✅ DONE |
| 6 | H10 | `server.py` — await cancelled tasks + close DB on shutdown | ✅ DONE |
| 7 | M11 | `api.py` — remove `__import__('time')` anti-pattern | ✅ DONE |
| 8 | C4 | `api.py` — restrict `browse_dirs` to home directory | ✅ DONE |
| 9 | C5 | `api.py` — whitelist `persist_settings` keys | ✅ DONE |
| 10 | H5 | `api.py` — Pydantic `CreateScheduleRequest` + HH:MM validator | ✅ DONE |
| 11 | H2 | `orchestrator.py` — `list(conversation_log)` snapshot in _detect_stuck | ✅ DONE |
| 12 | H12 | `events.py` — async lock in subscribe/unsubscribe/publish | ✅ DONE |
| 13 | H3/H4 | `session_manager.py` — proper methods; raw SQL removed from api.py | ✅ DONE |
| 14 | M1 | `api.py` — optional `DASHBOARD_API_KEY` X-API-Key middleware | ✅ DONE |
| 15 | M3 | `orchestrator.py` + `api.py` — all subprocess.run → asyncio.create_subprocess_exec | ✅ DONE |
| 16 | M6 | `orchestrator.py` — log capped at 2000; `_agents_used` set | ✅ DONE |
| 17 | M7 | Dockerfile + docker-compose + .dockerignore hardened | ✅ DONE |
| 18 | M9 | `requirements.txt` — all packages pinned with `==` | ✅ DONE |
| 19 | A1 | Extract services layer (api.py → services/) | 🔲 Future sprint |
| 20 | A3 | Pydantic response models on all endpoints | 🔲 Future sprint |
| 21 | F5 | Enhanced health check (DB, CLI binary, disk space) | ✅ DONE |
| 22 | F6 | Rate limiting middleware (per-IP, slowapi or custom) | 🔲 Future sprint |

---

## Future Sprint (3 items remaining)

### F6 — Rate limiting middleware
Protect `/api/` endpoints from abuse. Use `slowapi` or a simple in-memory per-IP token bucket.
**Effort**: 1h

### A1 — Extract services layer
`api.py` mixes routing, business logic, git commands, and dict formatting.
Split into `services/project_service.py`, `services/agent_service.py`.
**Effort**: 2-3h

### A3 — Pydantic response models
All 25+ endpoints return raw `dict`. Add typed response models for OpenAPI + validation.
**Effort**: 2h

---

## Extra Fixes Done This Session (not in original plan)

| Fix | File | Description |
|-----|------|-------------|
| STANDBY agents | ProjectView.tsx + orchestrator.py | delegation → `working` immediately; done/error preserved post-task |
| Page refresh survival | orchestrator.py | `_send_final()` persists to SQLite before WS publish |
| Poll failsafe | ProjectView.tsx | 10s poll merges server agent_states to recover missed WS events |
| Smart skill selection | skills_registry.py | `select_skills_for_task()` — top-5 by keyword score |
| Rate limiting | orchestrator.py + config.py | 3s gap between orchestrator LLM calls |
| conversation_log cap | orchestrator.py | Max 2000 entries; `_agents_used` set for O(1) tracking |
| Budget warning | orchestrator.py | Progressive alert at 80% (`BUDGET_WARNING_THRESHOLD`) |
| Config overrides | config.py | All settings use `_get()` for `settings_overrides.json` |
| TASK_COMPLETE gate | orchestrator.py | Rejected completion runs accompanying delegate blocks |
| Stuck detector | orchestrator.py | Scans full log (not just last 8 entries) |
| Session context reset | orchestrator.py | `shared_context` + `_agents_used` cleared between tasks |
| EPIC continuations | orchestrator.py | Manifest presence no longer downgrades EPIC → LARGE |
| Unknown role feedback | orchestrator.py | `⚠ Invalid Role` error entry injected with valid roles list |
| inject_user_message | orchestrator.py | Routes to actual target agent; increments `turn_count` |
| Orchestrator skills | orchestrator.py | Skill content injected into orchestrator system prompt |
| CLI auth cleanup | sdk_client.py | Entire Meta x2p proxy block deleted; no API key references anywhere |
| Error message | orchestrator.py | API key reference replaced with `claude login` guidance |
