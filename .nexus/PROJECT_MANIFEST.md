# PROJECT MANIFEST — Web Claude Bot

## Overview
Multi-agent orchestration dashboard for Claude AI. FastAPI backend with WebSocket support, SQLite persistence, and a React/Vite frontend.

**Core principle**: All agents call **Claude CLI directly** via `claude_agent_sdk` (subprocess). No API key, no proxy — Claude authenticates through its own credentials (`claude login`).

## Architecture
- **server.py** — Entry point, uvicorn setup, startup/shutdown lifecycle
- **config.py** — Configuration constants, env/file overrides, `validate_config()`
- **state.py** — Global singleton state (SDK client, session manager, active sessions)
- **orchestrator.py** — OrchestratorManager: main orchestration loop, delegation parsing, sub-agent execution
- **sdk_client.py** — ClaudeSDKManager: calls `claude_agent_sdk` (CLI subprocess), retry logic, error classification
- **session_manager.py** — SessionManager: SQLite CRUD for projects, sessions, messages, tasks, schedules
- **skills_registry.py** — Skill discovery, smart selection (top-5 by keyword score), prompt building
- **scheduler.py** — Periodic task scheduler (cron-like)
- **dashboard/api.py** — FastAPI REST endpoints + WebSocket (25+ endpoints)
- **dashboard/events.py** — EventBus pub/sub for real-time event broadcasting

## SDK / Claude CLI
- `claude_agent_sdk.query()` → spawns `claude` binary as subprocess
- Auth: `claude login` credentials — no API key, no proxy, no tokens
- `CLAUDECODE` env var unset before spawning (avoids nested-session error)
- macOS: uses `/usr/local/bin/claude_code/claude` (avoids sandbox-exec)
- Docker/Linux: `shutil.which("claude")` → finds on $PATH

## All Issues — Final Status (2026-03-08)

| ID | Description | Status |
|----|-------------|--------|
| C1 | api.py — broken `async with _get_db()` | ✅ FIXED |
| C2 | api.py — wrong attribute names | ✅ FIXED |
| C3 | sdk_client.py — hardcoded auth token | ✅ FIXED + deleted entire proxy block |
| C4 | api.py — browse_dirs directory traversal | ✅ FIXED: home dir restriction |
| C5 | api.py — persist_settings arbitrary JSON | ✅ FIXED: `_ALLOWED_PERSIST_KEYS` whitelist |
| C7 | session_manager.py — duplicate `close()` | ✅ FIXED: bare duplicate deleted |
| H1 | sdk_client.py — classify_error(None) crash | ✅ NOT A BUG |
| H2 | orchestrator.py — _detect_stuck without snapshot | ✅ FIXED: `list(conversation_log)` |
| H3/H4 | api.py — raw SQL bypasses SessionManager | ✅ FIXED: proper SM methods added |
| H5 | api.py — create_schedule no validation | ✅ FIXED: Pydantic + HH:MM validator |
| H7 | orchestrator.py — `results` used before assignment | ✅ FIXED: moved before loop |
| H8 | state.py — dict iteration without snapshot | ✅ FIXED: `dict(sessions).items()` |
| H9 | config.py — case sensitivity in `_get()` | ✅ FIXED: config.py rewritten |
| H10 | server.py — tasks cancelled but not awaited | ✅ FIXED: await + DB close in finally |
| H11 | config.py — validate_config() never called | ✅ FIXED: called at startup |
| H12 | events.py — subscribe/unsubscribe ignore lock | ✅ FIXED: async lock in all methods |
| M1 | No authentication on any endpoint | ✅ FIXED: optional `DASHBOARD_API_KEY` middleware |
| M3 | subprocess.run() blocking in async context | ✅ FIXED: all replaced with asyncio.create_subprocess_exec |
| M6 | conversation_log unbounded | ✅ FIXED: capped at 2000, `_agents_used` set |
| M7 | Docker — no port mapping, healthcheck | ✅ FIXED: Dockerfile + docker-compose hardened |
| M9 | requirements.txt — no pinned versions | ✅ FIXED: all packages pinned with `==` |
| M11 | api.py — `__import__('time')` anti-pattern | ✅ FIXED: `time.time()` everywhere |
| F5 | No enhanced health check | ✅ FIXED: DB, CLI binary, disk space check |
| F6 | Rate limiting middleware | 🔲 Future sprint |
| A1 | Extract services layer from api.py | 🔲 Future sprint |
| A3 | Pydantic response models on all endpoints | 🔲 Future sprint |

## Key Improvements This Session (2026-03-08)

### Agent State / UI
- **STANDBY bug**: delegation → `state='working'` immediately; `done`/`error` preserved after task completes
- **project_status 'running'**: resets agent cards cleanly for new task
- **Page refresh**: `_send_final()` persists to SQLite first → result survives WS disconnect
- **Poll failsafe**: 10s `loadProject` poll merges server `agent_states` → recovers missed WS events

### Orchestration Logic
- **Smart skill selection**: top-5 by keyword score (~90% token reduction per call)
- **Rate limiting**: 3s gap between orchestrator LLM calls
- **TASK_COMPLETE gate**: rejected completions now run accompanying delegate blocks
- **Stuck detector**: scans full log (not just last 8 entries)
- **Session context reset**: `shared_context` + `_agents_used` cleared between tasks
- **EPIC continuations**: manifest presence no longer downgrades EPIC → LARGE
- **Unknown role feedback**: `⚠ Invalid Role` error injected with valid roles list
- **inject_user_message**: routes to actual target; increments `turn_count`
- **Orchestrator skills**: skill content injected into orchestrator system prompt

### Config / Infrastructure
- **Config overrides**: all settings use `_get()` for `settings_overrides.json`
- **Budget warning**: progressive alert at 80% threshold
- **validate_config()**: called at startup, exits on invalid config
- **Shutdown**: background tasks awaited + DB closed cleanly
- **CLI auth**: removed all API key / proxy / x2p references entirely

## DevOps
| File | Status |
|------|--------|
| Dockerfile | ✅ Multi-stage build, non-root user, HEALTHCHECK |
| docker-compose.yml | ✅ Port mapping, health check, resource limits |
| .dockerignore | ✅ Excludes node_modules, .git, venv, dist |
| .env.example | ✅ All env vars documented, no API key |
| restart.sh | ✅ Build + restart (macOS local dev) |

## Test Coverage
- tests/: conftest, test_store, test_state, test_config_full, test_orchestrator, test_skills_registry, test_proof
- **131 tests**, all passing in ~0.36s
- **Gap**: No API endpoint tests, no WebSocket tests, no integration tests

## Next Steps — see `.nexus/IMPROVEMENT_PLAN.md`
- **3 P0 security fixes**: message length validation, symlink-safe path traversal, project dir restriction
- **11 P1 items**: WebSocket auth, connection pool init, scheduler dedup, blocking I/O, git caching, refactors
- **14 P2 items**: code quality, performance, maintainability improvements

## Code Review — Quality Audit (2026-03-08)
Full report: `.nexus/CODE_REVIEW.md`

| Severity | Count | Key Findings |
|----------|-------|--------------|
| CRITICAL | 0 | — |
| HIGH | 3 | Missing validation on settings/budget endpoints, dynamic SQL field names |
| MEDIUM | 5 | Lock contention in orchestrator, unvalidated persist values, scheduler edge cases |
| LOW | 10+ | f-string logging, missing type hints, naming, unbounded lists |

### Top Quick Wins:
1. Add Pydantic validators to `UpdateSettingsRequest` and `SetBudgetRequest` — prevent invalid config values
2. Add field name whitelist to `session_manager.update_project_fields()` — prevent SQL column injection
3. Move git subprocess calls outside `asyncio.Lock` in orchestrator — reduce agent contention
4. Extract API routers from monolithic `create_app()` in api.py — improve maintainability
5. Add request_id middleware for API traceability
