# Improvement Plan — Web Claude Bot

**Last updated**: 2026-03-08
**Scope**: All remaining improvement opportunities after 22 items completed this session.
**Organized by**: Code Quality · Performance · Security · Reliability · Maintainability

> Priority: **P0** = do now (bugs/security) · **P1** = do next sprint · **P2** = nice-to-have

---

## Previous Session — All 22 Items ✅ DONE

| ID | Description | File |
|----|-------------|------|
| C1 | `async with _get_db()` → `await _get_db()` | api.py |
| C2 | Wrong attribute names (shared_context, turn_count, total_cost_usd) | api.py |
| C3 | Hardcoded auth token → env var | sdk_client.py |
| C4 | browse_dirs directory traversal → home dir restriction | api.py |
| C5 | persist_settings arbitrary JSON → whitelist | api.py |
| C7 | Duplicate close() deleted | session_manager.py |
| H2 | `list(conversation_log)` snapshot in _detect_stuck | orchestrator.py |
| H3/H4 | Raw SQL → proper SessionManager methods | api.py + session_manager.py |
| H5 | Pydantic CreateScheduleRequest + HH:MM validator | api.py |
| H7 | `results={}` moved before loop | orchestrator.py |
| H8 | Dict snapshots in all iterations | state.py |
| H10 | Await cancelled tasks + close DB on shutdown | server.py |
| H11 | `validate_config()` called at startup | server.py |
| H12 | Async lock in subscribe/unsubscribe/publish | events.py |
| M1 | Optional DASHBOARD_API_KEY middleware | api.py |
| M3 | subprocess.run → asyncio.create_subprocess_exec | orchestrator.py, api.py |
| M6 | conversation_log capped at 2000 + `_agents_used` set | orchestrator.py |
| M7 | Dockerfile + docker-compose + .dockerignore hardened | DevOps files |
| M9 | requirements.txt pinned with `==` | requirements.txt |
| M11 | `__import__('time')` anti-pattern removed | api.py |
| F5 | Enhanced health check (DB, CLI, disk) | api.py |
| + 17 extra fixes | (see PROJECT_MANIFEST.md) | various |

---

## 1. Security

### SEC-01 · No input length validation on `send_message`
| | |
|---|---|
| **File** | `dashboard/api.py` |
| **Lines** | 829–864 (`send_message` endpoint) |
| **Problem** | `req.message` is never checked against `MAX_USER_MESSAGE_LENGTH`. An attacker can send multi-MB messages that get forwarded to the SDK, stored in DB, and kept in `conversation_log` memory. |
| **Fix** | Add length check at the top of `send_message`: `if len(req.message) > cfg.MAX_USER_MESSAGE_LENGTH: return JSONResponse({"error": f"Message too long (max {cfg.MAX_USER_MESSAGE_LENGTH})"}, 400)`. Same for `talk_agent`. |
| **Priority** | **P0** |

### SEC-02 · `read_file` path traversal via symlinks
| | |
|---|---|
| **File** | `dashboard/api.py` |
| **Lines** | 1062–1092 (`read_file` endpoint) |
| **Problem** | Path traversal check uses `str(file_path).startswith(str(proj_resolved))`. This is vulnerable to: (1) symlink attacks where a symlink inside the project points to `/etc/shadow`, (2) prefix collisions (`/home/user/project-secret` matches `/home/user/project`). |
| **Fix** | Use `file_path.resolve().is_relative_to(proj_resolved)` (Python 3.9+) instead of string prefix matching. Add `+ os.sep` to the startswith check as a fallback. Reject symlinks: `if file_path.is_symlink(): return {"error": "Symlinks not allowed"}`. |
| **Priority** | **P0** |

### SEC-03 · `create_project` directory creation not restricted
| | |
|---|---|
| **File** | `dashboard/api.py` |
| **Lines** | 538–590 (`create_project` endpoint) |
| **Problem** | `os.makedirs(project_dir, exist_ok=True)` creates directories at any user-supplied path. An attacker could create `/tmp/evil`, `/var/www/hack`, etc. The `browse_dirs` endpoint is restricted to home dir, but `create_project` is not. |
| **Fix** | Apply the same home-dir + PROJECTS_BASE_DIR restriction from `browse_dirs` to `create_project`. Resolve and validate the path before calling `os.makedirs`. |
| **Priority** | **P0** |

### SEC-04 · API key middleware doesn't protect WebSocket
| | |
|---|---|
| **File** | `dashboard/api.py` |
| **Lines** | 213–221 (API key middleware) + 1096–1145 (WebSocket) |
| **Problem** | The `DASHBOARD_API_KEY` middleware only checks HTTP requests (`@app.middleware("http")`). The WebSocket endpoint `/ws` is unprotected — any unauthenticated client can subscribe and receive all events (project names, agent states, costs, code snippets). |
| **Fix** | Check for the API key in the WebSocket handshake: extract from query parameter `?api_key=...` or from `Sec-WebSocket-Protocol` header, and reject unauthorized connections before `ws.accept()`. |
| **Priority** | **P1** |

### SEC-05 · `set_project_budget` no input validation
| | |
|---|---|
| **File** | `dashboard/api.py` |
| **Lines** | 989–997 (`set_project_budget` endpoint) |
| **Problem** | Accepts any float: `float(data.get("budget_usd", 0))`. Could be negative, NaN, or Infinity. A negative budget would immediately trigger budget-exceeded logic, locking out the project. |
| **Fix** | Validate: `if not (0 <= budget <= 10000): return JSONResponse({"error": "..."}, 400)`. Also use `math.isfinite()`. Use a Pydantic model instead of raw `request.json()`. |
| **Priority** | **P1** |

### SEC-06 · `update_project_fields` allows arbitrary column names
| | |
|---|---|
| **File** | `session_manager.py` |
| **Lines** | 360–368 (`update_project_fields` method) |
| **Problem** | Field names from `**kwargs` are interpolated directly into SQL via f-string: `f"{k}=?"`. While only called internally, a future developer could accidentally pass user-controlled field names, enabling SQL injection. |
| **Fix** | Whitelist allowed field names: `_ALLOWED_FIELDS = {"name", "description", "status", "project_dir", "away_mode", "budget_usd"}`. Reject any key not in the whitelist. |
| **Priority** | **P1** |

---

## 2. Reliability

### REL-01 · `agent_states` dict modified concurrently without lock
| | |
|---|---|
| **File** | `orchestrator.py` |
| **Lines** | 1364, 1478, 877, 946 (many locations) |
| **Problem** | Multiple concurrent sub-agents write to `self.agent_states[role]` and `self.current_agent` without holding the existing `lock`. While CPython's GIL makes dict assignment atomic, this is not guaranteed across Python implementations and can cause lost updates when two agents finish simultaneously. |
| **Fix** | Protect all `agent_states` / `current_agent` / `current_tool` writes with the existing `lock` from `_run_sub_agents`. Or replace with a purpose-built `AgentStateTracker` class that encapsulates the locking. |
| **Priority** | **P1** |

### REL-02 · `_pool` Semaphore created at module import time
| | |
|---|---|
| **File** | `sdk_client.py` |
| **Lines** | 192 (`_pool = _ConnectionPool(max_concurrent=5)`) |
| **Problem** | `asyncio.Semaphore()` is created at import time, potentially before any event loop exists. In Python 3.10+ with `asyncio.Runner`, this semaphore may be bound to a different (or no) event loop, causing `RuntimeError: ... attached to a different loop`. |
| **Fix** | Lazy-initialize the pool on first use: `_pool: _ConnectionPool | None = None` + `def _get_pool() -> _ConnectionPool`. Or create it inside `ClaudeSDKManager.__init__()`. |
| **Priority** | **P1** |

### REL-03 · Scheduler time matching can miss schedules
| | |
|---|---|
| **File** | `scheduler.py` |
| **Lines** | 13–21 (`scheduler_loop`) + 29–33 (`_check_due_schedules`) |
| **Problem** | Check interval is 60s, but if the loop wakes at HH:MM:50, the next check happens at HH:MM+1:50, skipping any schedule set for HH:MM+1. Also, if the check takes >10s (e.g., DB is slow), the same schedule could be triggered twice (no deduplication via `last_run` check). |
| **Fix** | (1) Check `last_run` in the `_check_due_schedules` query: `AND (last_run IS NULL OR last_run < ?)` with a cutoff of 2 minutes ago. (2) Use shorter interval (30s) or track the last-checked minute. (3) Always mark `last_run` even on failure to prevent re-trigger. |
| **Priority** | **P1** |

### REL-04 · `_should_retry` uses recursion instead of loop
| | |
|---|---|
| **File** | `orchestrator.py` |
| **Lines** | 1292–1295 |
| **Problem** | `return await self._run_orchestrator(user_message)` is a recursive tail call. With `_MAX_ANYIO_RETRIES=3`, worst case stacks 3 frames deep. Each frame holds the full `try/except/finally` context (including local variables like `delegations`, `sub_results`, etc.), wasting memory and making debugging harder. |
| **Fix** | Wrap the entire `_run_orchestrator` body in `while True:` and use `continue` instead of recursion. Set `_should_retry = False` at loop top. |
| **Priority** | **P2** |

### REL-05 · `conversation_log` appended without lock
| | |
|---|---|
| **File** | `orchestrator.py` |
| **Lines** | 581, 711 (append calls) + `_record_response` (called under lock sometimes, not always) |
| **Problem** | `self.conversation_log.append()` is called from `inject_user_message()` (no lock) and from `_run_orchestrator()` (no lock), while sub-agents call `_record_response()` under lock. Mixed locking discipline could cause corruption with async gather. |
| **Fix** | Create a dedicated `_log_message()` method that always acquires the lock before appending. |
| **Priority** | **P2** |

### REL-06 · EventBus subscriber queue leak on crash
| | |
|---|---|
| **File** | `dashboard/events.py` |
| **Lines** | 23–36 |
| **Problem** | If a WebSocket handler crashes without calling `unsubscribe()`, the queue remains in `_subscribers` forever — consuming memory and wasting CPU on `publish()` calls that put events into a queue nobody reads. |
| **Fix** | Add periodic cleanup: in `publish()`, check if queue is full AND was full last time → auto-remove it. Or add a `subscriber_count` property and log warnings when count exceeds expected max. Or use `weakref` for subscriber tracking. |
| **Priority** | **P2** |

### REL-07 · Single aiosqlite connection shared across all requests
| | |
|---|---|
| **File** | `session_manager.py` |
| **Lines** | 173–228 |
| **Problem** | One `aiosqlite.Connection` serves all concurrent async requests. While SQLite with WAL mode supports concurrent reads, concurrent writes still serialize at the SQLite level. Under heavy load with many simultaneous `commit()` calls, performance degrades and `OperationalError: database is locked` can occur. The `_retry_on_db_error` decorator helps, but adding a second connection would be better. |
| **Fix** | Use a small connection pool (2-3 connections) or separate read/write connections. Alternatively, batch commits using a write queue. |
| **Priority** | **P2** |

---

## 3. Performance

### PERF-01 · `_get_workspace_context()` is blocking I/O in async context
| | |
|---|---|
| **File** | `orchestrator.py` |
| **Lines** | 518–546 |
| **Problem** | `Path.iterdir()` and `item.is_dir()` are synchronous filesystem calls that block the event loop. With large project directories (thousands of files), this stalls all concurrent operations. |
| **Fix** | Wrap in `asyncio.to_thread()` or cache the result per orchestrator round (workspace doesn't change mid-round). |
| **Priority** | **P1** |

### PERF-02 · `_read_project_manifest()` is blocking I/O
| | |
|---|---|
| **File** | `orchestrator.py` |
| **Lines** | 354–371 |
| **Problem** | `manifest_path.read_text()` blocks the event loop. Called in every orchestrator loop iteration AND in `_check_premature_completion()`. |
| **Fix** | Use `asyncio.to_thread(manifest_path.read_text, encoding="utf-8")` or `aiofiles`. Cache for the duration of one orchestrator loop. |
| **Priority** | **P1** |

### PERF-03 · Redundant git subprocess calls per round
| | |
|---|---|
| **File** | `orchestrator.py` |
| **Lines** | 496 (`_detect_file_changes`), 1017, 1115, 1774–1798 (`_accumulate_context`) |
| **Problem** | Each sub-agent completion triggers 3 git commands in `_accumulate_context()`. Then `_detect_file_changes()` runs again in the rejection handler, nudge handler, and final summary. A single round with 3 agents spawns ~12 git subprocesses for overlapping data. |
| **Fix** | Cache git diff results per round. Create `_cached_git_status(ttl=10)` that reuses results within 10 seconds. Clear cache at loop start. |
| **Priority** | **P1** |

### PERF-04 · `list_projects()` does O(A×D) merge
| | |
|---|---|
| **File** | `dashboard/api.py` |
| **Lines** | 271–324 |
| **Problem** | For each active project, iterates through ALL DB projects to find matching metadata (lines 292-298). With 50 active projects and 200 DB projects, that's 10,000 comparisons. |
| **Fix** | Build a `dict` from `db_projects` keyed by `project_id` first: `db_map = {p["project_id"]: p for p in db_projects}`. Then look up in O(1). |
| **Priority** | **P2** |

### PERF-05 · Messages table grows unbounded
| | |
|---|---|
| **File** | `session_manager.py` |
| **Lines** | 275–289 (add_message — no archival) |
| **Problem** | Every agent response is stored as a message row. A long-running project can accumulate 10,000+ messages. `list_projects()` joins messages for counts on every page load. `get_messages_paginated()` uses `OFFSET` which is O(N) in SQLite. |
| **Fix** | (1) Add message archival: move messages older than N days to an `archived_messages` table. (2) Cache message counts in the `projects` table. (3) Use cursor-based pagination (timestamp-based) instead of `OFFSET`. |
| **Priority** | **P2** |

### PERF-06 · f-strings in logger calls evaluated eagerly
| | |
|---|---|
| **File** | All files (orchestrator.py, sdk_client.py, api.py, etc.) |
| **Lines** | Throughout |
| **Problem** | `logger.info(f"...")` evaluates the f-string even when INFO logging is disabled. With debug-level messages that format large objects, this wastes CPU. |
| **Fix** | Use lazy formatting: `logger.info("Message: %s, cost=%.4f", msg, cost)`. Low priority since most are INFO/WARNING which are typically enabled. |
| **Priority** | **P2** |

---

## 4. Code Quality

### CQ-01 · `stop()` has debug traceback leftover
| | |
|---|---|
| **File** | `orchestrator.py` |
| **Lines** | 629–632 |
| **Problem** | `import traceback` + `traceback.format_stack(limit=4)` runs on every `stop()` call, logging a 4-frame stack trace. This was likely added for debugging and left in. In production, every project stop logs unnecessary stack frames. |
| **Fix** | Remove the `import traceback` and `caller = ...` lines. Keep only the `logger.info(f"[{self.project_id}] stop() called")`. |
| **Priority** | **P1** |

### CQ-02 · Private attribute access from api.py into OrchestratorManager
| | |
|---|---|
| **File** | `dashboard/api.py` |
| **Lines** | 111 (`_message_queue`), 393 (`_current_loop`), 413 (`_background_tasks`) |
| **Problem** | Multiple API endpoints access private `_`-prefixed attributes of `OrchestratorManager`. This couples the API layer to internal implementation details and breaks encapsulation. |
| **Fix** | Add public properties to `OrchestratorManager`: `@property pending_message_count`, `@property current_loop`, `@property background_task_count`. Update api.py to use these instead. |
| **Priority** | **P1** |

### CQ-03 · `_estimate_task_complexity` uses brittle keyword matching
| | |
|---|---|
| **File** | `orchestrator.py` |
| **Lines** | 373–417 |
| **Problem** | 30+ hardcoded keyword strings (including Hebrew) with no tests. Easy to get false positives (e.g., "implement" matches "MEDIUM" but "implement a complete system" should be EPIC). Order-dependent: EPIC patterns checked first, but no scoring. |
| **Fix** | (1) Extract to a separate module `complexity.py` with unit tests. (2) Use a scoring system: count pattern matches per category, pick the highest score. (3) Add tests for edge cases. |
| **Priority** | **P2** |

### CQ-04 · `sdk_client.py: _consume_stream` is 200+ lines
| | |
|---|---|
| **File** | `sdk_client.py` |
| **Lines** | 378–616 |
| **Problem** | Single method handles: stream iteration, text extraction, tool use parsing, display formatting, callback dispatch, partial message assembly, generator lifecycle, error handling. Too many responsibilities for one method. |
| **Fix** | Extract: `_format_tool_use(block) -> str`, `_assemble_text(text_parts, turn_text) -> None`, `_handle_result_message(message) -> SDKResponse`. Keep `_consume_stream` as the coordinator. |
| **Priority** | **P2** |

### CQ-05 · Duplicate default dicts in `PREDEFINED_PROJECTS`
| | |
|---|---|
| **File** | `config.py` |
| **Lines** | 209–223 |
| **Problem** | The same default dict `{"web-claude-bot": "~/...", "family-finance": "~/..."}` is written twice — once for JSON parse failure fallback (line 215) and once for missing env var (line 220). |
| **Fix** | Extract to a constant: `_DEFAULT_PROJECTS = {...}`. Use it in both branches. |
| **Priority** | **P2** |

### CQ-06 · No docstrings on most API endpoints
| | |
|---|---|
| **File** | `dashboard/api.py` |
| **Lines** | Throughout |
| **Problem** | Most endpoints have one-line docstrings (e.g., "Pause project.") that don't describe request/response schemas, error cases, or side effects. FastAPI uses these for OpenAPI docs. |
| **Fix** | Add proper docstrings with Args/Returns/Raises sections, or (better) use Pydantic response models which auto-generate docs. |
| **Priority** | **P2** |

---

## 5. Maintainability

### MAINT-01 · `api.py` is a 1160-line monolith
| | |
|---|---|
| **File** | `dashboard/api.py` |
| **Lines** | 1–1161 |
| **Problem** | All 25+ endpoints, helper functions, middleware, WebSocket handler, and static file serving are defined inside a single `create_app()` function. Finding, testing, or modifying any endpoint requires scrolling through 1100+ lines. |
| **Fix** | Split into FastAPI routers: `dashboard/routes/projects.py`, `dashboard/routes/agents.py`, `dashboard/routes/schedules.py`, `dashboard/routes/settings.py`, `dashboard/routes/websocket.py`. Use `app.include_router()` in `create_app()`. |
| **Priority** | **P1** |
| **Effort** | 2–3 hours |

### MAINT-02 · `orchestrator.py` is 1800+ lines
| | |
|---|---|
| **File** | `orchestrator.py` |
| **Lines** | 1–1800+ |
| **Problem** | `_run_orchestrator()` alone is ~600 lines. `_run_sub_agents()` is ~400 lines. These make the file nearly impossible to review or test in isolation. |
| **Fix** | Extract: `SubAgentExecutor` class (handles parallel execution, retries, file conflict detection), `TaskComplexityEstimator` (task classification + premature completion checks), `ContextBuilder` (workspace listing, manifest reading, prompt assembly). |
| **Priority** | **P1** |
| **Effort** | 3–4 hours |

### MAINT-03 · Prompts are 400+ lines of string constants in config.py
| | |
|---|---|
| **File** | `config.py` |
| **Lines** | 236–652 |
| **Problem** | `ORCHESTRATOR_SYSTEM_PROMPT`, `SOLO_AGENT_PROMPT`, `SUB_AGENT_PROMPTS` are massive string concatenations totaling 400+ lines. Editing prompts requires navigating Python string escape rules. Prompts can't be version-controlled independently or loaded by non-Python tools. |
| **Fix** | Move prompts to `prompts/orchestrator.md`, `prompts/developer.md`, etc. Load at startup: `ORCHESTRATOR_SYSTEM_PROMPT = (Path("prompts/orchestrator.md")).read_text()`. |
| **Priority** | **P1** |
| **Effort** | 1 hour |

### MAINT-04 · No API endpoint tests
| | |
|---|---|
| **File** | `tests/` |
| **Lines** | N/A |
| **Problem** | 131 unit tests exist for state, config, and skills_registry, but zero tests for HTTP endpoints or WebSocket. The most critical code (api.py) has no automated coverage. Regressions in API behavior go undetected. |
| **Fix** | Add `tests/test_api.py` using FastAPI's `TestClient`. Mock `state.sdk_client` and `state.session_mgr`. Test: project CRUD, message sending, settings, health check, error responses. |
| **Priority** | **P1** |
| **Effort** | 3–4 hours |

### MAINT-05 · No mypy / type checking in CI
| | |
|---|---|
| **File** | Project root |
| **Lines** | N/A |
| **Problem** | Type hints exist throughout the codebase but are never validated. Wrong type annotations (e.g., claiming `str` when it's actually `str | None`) would go undetected. |
| **Fix** | Add `mypy.ini` or `[tool.mypy]` in `pyproject.toml`. Start with `--ignore-missing-imports --no-strict`. Run in CI. Fix errors incrementally. |
| **Priority** | **P2** |
| **Effort** | 1 hour setup + ongoing |

### MAINT-06 · No structured logging or log rotation
| | |
|---|---|
| **File** | `server.py` |
| **Lines** | 20–24 (`logging.basicConfig`) |
| **Problem** | Logs go to stdout only, with no rotation. In production, logs grow unbounded. No structured format (JSON) for log aggregation tools. No separate error log file. |
| **Fix** | Use `logging.handlers.RotatingFileHandler` (10MB, 5 backups). Add JSON formatter for production mode (configurable via `LOG_FORMAT=json` env var). |
| **Priority** | **P2** |
| **Effort** | 1 hour |

### MAINT-07 · No database migration system
| | |
|---|---|
| **File** | `session_manager.py` |
| **Lines** | 684–696 (`_migrate_add_columns`) |
| **Problem** | Schema migrations use try/except `SELECT` to detect missing columns. No version tracking, no rollback, no migration ordering. Adding a new column requires editing `_migrate_add_columns` with another try/except block. |
| **Fix** | Add a `schema_version` table. Track current version as an integer. Write numbered migration functions. On startup, run all migrations > current version. |
| **Priority** | **P2** |
| **Effort** | 2 hours |

### MAINT-08 · Inconsistent error response format
| | |
|---|---|
| **File** | `dashboard/api.py` |
| **Lines** | Various (502, 661, 1025, etc.) |
| **Problem** | Some endpoints return `{"error": "..."}` with HTTP 200 (e.g., `get_files` line 502, `get_file_tree` line 1025). Others use proper `JSONResponse(status_code=4xx)`. Frontend has to check both HTTP status and JSON `error` field. |
| **Fix** | Standardize: always use `JSONResponse({"error": "message"}, status_code=NNN)`. Add a FastAPI exception handler for consistent error envelope: `{"error": "message", "status": NNN}`. |
| **Priority** | **P2** |
| **Effort** | 1 hour |

---

## Summary — Priority Matrix

| Priority | Count | Items |
|----------|-------|-------|
| **P0** | 3 | SEC-01, SEC-02, SEC-03 |
| **P1** | 11 | SEC-04, SEC-05, SEC-06, REL-01, REL-02, REL-03, PERF-01, PERF-02, PERF-03, CQ-01, CQ-02, MAINT-01, MAINT-02, MAINT-03, MAINT-04 |
| **P2** | 14 | REL-04, REL-05, REL-06, REL-07, PERF-04, PERF-05, PERF-06, CQ-03, CQ-04, CQ-05, CQ-06, MAINT-05, MAINT-06, MAINT-07, MAINT-08 |

### Recommended Execution Order

1. **P0 Security fixes** (SEC-01, SEC-02, SEC-03) — ~1 hour total
2. **P1 Security hardening** (SEC-04, SEC-05, SEC-06) — ~1 hour
3. **P1 Reliability** (REL-01, REL-02, REL-03) — ~2 hours
4. **P1 Performance** (PERF-01, PERF-02, PERF-03) — ~2 hours
5. **P1 Code Quality** (CQ-01, CQ-02) — ~30 minutes
6. **P1 Maintainability refactors** (MAINT-01..04) — ~10 hours (can be done incrementally)
7. **P2 items** — backlog, address as part of ongoing development
