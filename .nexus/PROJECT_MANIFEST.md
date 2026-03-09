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
| S1 | api.py — SPA path traversal via `/{full_path:path}` | ✅ FIXED: `.resolve()` + `is_relative_to()` |
| S3 | api.py — API key timing attack | ✅ FIXED: `hmac.compare_digest()` in middleware + WS |
| S5 | config.py — CORS wildcard `*` default | ✅ FIXED: default `localhost:5173,localhost:8080` |
| S6 | orchestrator.py — unbounded recursion in retry | ✅ FIXED: cumulative `_retry_count` parameter |
| S8 | server.py — binds `0.0.0.0` by default | ✅ FIXED: default `127.0.0.1`, env override |
| S12 | events.py — shared mutable event dict | ✅ FIXED: shallow copy `{**event}` |
| S13 | scheduler.py — naive `datetime.now()` | ✅ FIXED: `ZoneInfo('Asia/Jerusalem')`, env configurable |
| DB | session_manager.py — missing indexes | ✅ FIXED: 3 new indexes added |
| BIO | orchestrator.py — blocking `Path.iterdir()`/`read_text()` | ✅ FIXED: `asyncio.to_thread()` wrapping |
| SV | api.py — update_settings missing cross-field validation | ✅ FIXED: budget relationship + timeout cap |

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
- tests/: conftest, test_store, test_state, test_state_full, test_config_full, test_skills_registry, test_proof
- tests/: test_session_manager_comprehensive, test_sdk_client, test_api_endpoints (NEW)
- **292 tests**, all passing in ~1.20s
- **Coverage areas**:
  - API endpoints: 51 tests (health, projects CRUD, messages, lifecycle, settings, schedules, stats, browse/read, security)
  - SDK client: 50 tests (error classification, SDKResponse, ConnectionPool, ErrorCategory, retry logic)
  - Session manager: 47 tests (project/message/session/task CRUD, budget, notifications, schedules, away mode, lifecycle)
  - State module: 38 tests (globals, project name regex, get/register/unregister manager)
  - Config: 29 tests (int/float/path/collection configs, system prompts, get helper)
  - Skills registry: 18 tests (scan, list, get content, get for agent, build prompt)
  - Store: 5 tests (save/load, messages, list, session CRUD, status)
  - Proof/smoke: 5 tests (imports, basic operations)
- **Security tests included**: SEC-01 (message length), SEC-03 (project path restriction), browse_dirs traversal, read_file traversal, persist_settings whitelist
- **Gap**: No WebSocket tests, no end-to-end integration tests

## Next Steps

### Bug Fixes & Security — see `.nexus/IMPROVEMENT_PLAN.md`
- **3 P0 security fixes**: message length validation, symlink-safe path traversal, project dir restriction
- **11 P1 items**: WebSocket auth, connection pool init, scheduler dedup, blocking I/O, git caching, refactors
- **14 P2 items**: code quality, performance, maintainability improvements

### Architecture & Hardening — see `.nexus/IMPROVEMENT_ROADMAP.md`
- **42 improvements** across 5 categories: Performance, Reliability, Observability, DX, Deployment
- **12 Tier 1 quick wins** (4-6 hours total): SQLite PRAGMAs, log rotation, heartbeat, Makefile, pre-commit hooks
- **16 Tier 2 high-value items** (20-30 hours): circuit breaker, structlog, metrics, API tests, caching
- **4 Tier 3 architectural items** (15-20 hours): DB connection separation, session recovery, TypeScript strict
- 4-week phased implementation plan with recommended execution order

### Competition Strategy — see `.nexus/COMPETITION_STRATEGY.md`
- README rewrite template with hook → problem → solution → visual proof structure
- 90-second demo flow choreography (timed sequence for live presentations)
- Competitive matrix vs CrewAI, AutoGen, LangGraph, Devin, Bedrock, Vertex AI
- Branding recommendation: rename to **Nexus** (already self-adopted in codebase via `.nexus/`)
- 48-hour action plan: 12 items prioritized by impact (demo GIF, README, architecture docs)

## Security Audit — 2026-03-08
Full report: `.nexus/SECURITY_AUDIT.md`

| Severity | Count | Key Findings |
|----------|-------|--------------|
| CRITICAL | 2 | SPA path traversal (S1), bypassPermissions RCE (S2) |
| HIGH | 5 | Timing attack (S3), settings DoS (S4), CORS wildcard (S5), unbounded recursion (S6), API key in URL (S7) |
| MEDIUM | 8 | 0.0.0.0 bind (S8), persist validation (S9), unbounded limit (S10), error leaks (S11), shared mutable event (S12), timezone (S13), TOCTOU race (S14), global env mutation (S15) |
| LOW | 5 | Unbounded queue, dead import, debug traceback, uninitialized attr, inconsistent constants |

### Top Priority Fixes:
1. **S1**: SPA `serve_spa()` path traversal — 5-line fix with `.resolve()` + `is_relative_to()`
2. **S3**: `hmac.compare_digest()` for API key — 2-line fix
3. **S8**: Default `host="127.0.0.1"` — 1-line fix
4. **S5**: CORS default to localhost — 1-line fix
5. **S4**: Pydantic Field validators on `UpdateSettingsRequest` — 7-line fix

### Previously Fixed (Verified ✅):
- SQL column injection → `_UPDATABLE_PROJECT_FIELDS` whitelist
- Budget endpoint → Pydantic `SetBudgetRequest` with bounds
- `get_manager()` race → inner dict snapshot
- Path traversal in `read_file` → `.resolve()` + `is_relative_to()`
- Browse-dirs traversal → home dir restriction
- Persist settings → `_ALLOWED_PERSIST_KEYS` whitelist

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
2. Add field name whitelist to `session_manager.update_project_fields()` — prevent SQL column injection ✅ Done
3. Move git subprocess calls outside `asyncio.Lock` in orchestrator — reduce agent contention
4. Extract API routers from monolithic `create_app()` in api.py — improve maintainability
5. Add request_id middleware for API traceability ✅ Done

## Frontend Review — Competition Polish (2026-03-08)
Full report: `.nexus/FRONTEND_REVIEW.md`

**Current state**: 7/10 — Professional "Mission Control" design system, real-time WebSocket, dual mobile/desktop layouts
**With recommended fixes**: 9/10

| Priority | Count | Key Items |
|----------|-------|-----------|
| P0 (1 hour) | 4 | Loading skeletons, animated cost counter, empty states, error boundary |
| P1 (5 hours) | 5 | Toast notifications, cost charts, sound effects, delegation animation, page transitions |
| P2 (5 hours) | 4 | Keyboard shortcuts, mobile menu, a11y fixes, performance optimization |

### What's Already Great:
- "Mission Control" dark theme with glow effects, scanlines, custom animations
- Real-time agent constellation/flow visualization (SVG, orbital animation)
- WebSocket with event replay and reconnection backoff
- Mobile-responsive with separate layouts, safe-area handling, haptic feedback
- Chat-like activity feed with error translation and code block rendering
- 14 well-structured components with proper separation of concerns

### Top 5 Demo Fixes (~5 hours total):
1. **Skeleton loading + empty states** (1h) — eliminates ugly "Loading..." and blank screens
2. **Animated cost counter** (30m) — makes the dashboard feel alive during demos
3. **Error boundary** (30m) — prevents demo crashes on unexpected data
4. **Toast notifications** (1h) — app feels responsive to user actions
5. **Cost analytics chart** (2h) — shows product depth beyond live view
