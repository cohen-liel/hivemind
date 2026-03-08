# PROJECT MANIFEST — Web Claude Bot

## Overview
Multi-agent orchestration dashboard for Claude AI. FastAPI backend with WebSocket support, SQLite persistence, and a React/Vite frontend.

**Core principle**: All agents call **Claude CLI directly** via `claude_agent_sdk` (subprocess). No ANTHROPIC_API_KEY is used for agent calls — Claude authenticates through its own credentials.

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

## Issues Log (updated 2026-03-08 Round 3)

### FIXED (all confirmed in current code)
- ~~[C1] dashboard/api.py:591 — broken async with _get_db()~~ → FIXED
- ~~[C2] dashboard/api.py:598-600 — wrong attribute names~~ → FIXED
- ~~[C3] sdk_client.py:57 — Hardcoded auth token~~ → FIXED: uses `os.environ.get("CLAUDE_AUTH_TOKEN", "")`
- ~~[C4] dashboard/api.py — browse_dirs directory traversal~~ → FIXED: restricted to home dir
- ~~[C5] dashboard/api.py — persist_settings arbitrary JSON~~ → FIXED: `_ALLOWED_PERSIST_KEYS` whitelist
- ~~[C7] session_manager.py:225 — Duplicate close() shadows error handling~~ → FIXED: deleted bare duplicate
- ~~[H2] orchestrator.py — _detect_stuck reads log without copy~~ → FIXED: `list(self.conversation_log)`
- ~~[H3/H4] api.py — Raw SQL bypasses SessionManager~~ → FIXED: proper SM methods added
- ~~[H5] api.py — create_schedule no validation~~ → FIXED: Pydantic `CreateScheduleRequest` with HH:MM validator
- ~~[H7] orchestrator.py — results used before assignment~~ → FIXED: `results = {}` moved before loop
- ~~[H8] state.py — dict iteration without snapshot~~ → FIXED: `dict(active_sessions).items()`
- ~~[H9] config.py — case sensitivity in _get()~~ → FIXED: config.py rewritten
- ~~[H10] server.py — tasks cancelled but not awaited~~ → FIXED: await + DB close in finally
- ~~[H11] config.py — validate_config() never called~~ → FIXED: called at startup with ConfigError/SystemExit
- ~~[H12] events.py — subscribe/unsubscribe ignore _lock~~ → FIXED: async lock in all 3 methods
- ~~[M6] orchestrator.py — conversation_log unbounded~~ → FIXED: capped at 2000, `_agents_used` set tracks agents
- ~~[M7] Docker — no port mapping, healthcheck~~ → FIXED: Dockerfile + docker-compose improved
- ~~[M11] api.py — __import__('time').time() anti-pattern~~ → FIXED: `time.time()` everywhere

### OPEN (still needs implementation)
- [M1] No authentication on any API endpoint — optional `DASHBOARD_API_KEY` middleware
- [M3] subprocess.run() (blocking) in async context — orchestrator.py + api.py
- [M9] requirements.txt — no pinned versions
- [F5] Enhanced health check (DB ping, CLI binary check, disk space)
- [F6] Rate limiting middleware (per-IP)
- [A1] Extract services layer from api.py (large refactor)
- [A3] Pydantic response models on all endpoints (large refactor)

### NOT A BUG
- [H1] sdk_client.py — classify_error(None) → `if not error_message:` handles correctly

## SDK / Claude CLI Notes
- Uses `claude_agent_sdk.query()` → spawns `claude` binary as subprocess
- Auth: Claude Code credentials (no ANTHROPIC_API_KEY needed)
- `CLAUDECODE` env var is unset before spawning to avoid nested-session error
- macOS: uses `/usr/local/bin/claude_code/claude` natively (avoids sandbox-exec)
- Docker/Linux: finds `claude` on $PATH via `shutil.which()`

## Key Fixes Made This Session (2026-03-08)
- **STANDBY agents**: delegation events now optimistically set state='working'; done/error preserved after task
- **Page refresh survival**: `_send_final()` now persists to SQLite before WS publish
- **Poll failsafe**: 10s `loadProject` poll merges server agent_states to recover missed WS events
- **Smart skill selection**: top-5 skills by keyword score (~90% token reduction)
- **Rate limiting**: 3s gap between orchestrator calls
- **conversation_log cap**: 2000 entries, `_agents_used` set for O(1) agent tracking
- **Budget warning**: progressive alert at 80% threshold
- **Config overrides**: all settings now use `_get()` for settings_overrides.json support

## DevOps Status
| File | Status |
|------|--------|
| Dockerfile | ✅ Multi-stage build, non-root user, HEALTHCHECK |
| docker-compose.yml | ✅ Port mapping, health check, resource limits |
| .dockerignore | ✅ Excludes node_modules, .git, venv, dist |
| .env.example | ✅ All env vars documented |
| restart.sh | ✅ Build + restart script |

## Test Coverage
- tests/conftest.py, test_store.py, test_state.py, test_config_full.py, test_orchestrator.py, test_skills_registry.py, test_proof.py
- **Gap**: No API endpoint tests, no WebSocket tests, no integration tests

## Improvement Plan Priority (remaining)
See `.nexus/IMPROVEMENT_PLAN.md` for full details.
| # | ID | Status |
|---|-----|--------|
| 14 | M1 | 🔲 TODO |
| 15 | M3 | 🔲 TODO |
| 18 | M9 | 🔲 TODO |
| 21 | F5 | 🔲 TODO |
| 22 | F6 | 🔲 TODO |
| 19-20 | A1, A3 | 🔲 Large refactor — future sprint |
