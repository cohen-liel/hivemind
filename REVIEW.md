# Telegram Claude Bot — Code Review

**Date**: 2026-03-06
**Reviewer**: Architect Agent
**Total findings**: 32

---

## Summary

| Priority | Category | Count | Status |
|----------|----------|-------|--------|
| 🔴 Critical | Security | 3 | ✅ Fixed |
| 🟠 High | Code Quality | 4 | ✅ Fixed |
| 🟠 High | Error Handling | 3 | ✅ Fixed (3.1, 3.2) / Documented (3.3) |
| 🟡 Medium | Architecture | 5 | 📝 To Do |
| 🟡 Medium | Missing Features | 6 | 📝 To Do |
| 🟡 Medium | Configuration | 3 | 📝 To Do |
| 🟡 Medium | Testing | 4 | 📝 To Do |
| 🟢 Low | Docker/Deployment | 3 | 📝 To Do |

---

## Category 1: 🔴 SECURITY (Critical)

### 1.1 — Debug files committed to repo ✅ Fixed
**Files**: `out.json`, `out.txt`, `err.txt` exist in the repo root and contain Claude CLI response data (costs, session IDs).

**Fix applied**: Added `out.json`, `out.txt`, `err.txt` to `.gitignore`.

---

### 1.2 — No user access control ✅ Fixed
**Problem**: Any Telegram user can discover and use this bot. There's zero authentication.

**Fix applied**:
- Added `ALLOWED_USER_IDS` config option in `config.py` (comma-separated list from env var).
- Added `is_authorized(user_id)` check at the top of every handler in `bot.py`.
- If `ALLOWED_USER_IDS` is empty, all users are allowed (dev mode).
- Unauthorized users receive "⛔ Unauthorized. Contact the bot admin."

---

### 1.3 — Dangerous default budget ceiling ✅ Fixed
**Problem**: `config.py` `MAX_BUDGET_USD` defaults to `10000.0`. If someone forgets to set the env var, agents can rack up $10K.

**Fix applied**: Changed default to `5.0` to match `.env.example`.

---

## Category 2: 🟠 CODE QUALITY (High Priority)

### 2.1 — Massive code duplication for project initialization ✅ Fixed
**Problem**: The pattern of creating `AgentManager` + saving to store + setting `current_project` is duplicated 4+ times across `new_agents_count()`, `project_callback_handler()` (predefined and saved branches), and `switch_command()` (predefined and saved branches).

**Fix applied**: Extracted `_activate_project(user_id, chat_id, bot, project_id, project_name, project_dir, agents_count, description=None)` helper function. All call sites now use this single function.

---

### 2.2 — Custom .env parser when python-dotenv is installed ✅ Fixed
**Problem**: `config.py` has a hand-rolled `_load_dotenv()` that doesn't handle quoted values, multiline values, or variable expansion.

**Fix applied**: Replaced with `from dotenv import load_dotenv; load_dotenv()`. Added `python-dotenv>=1.0` to `requirements.txt`.

---

### 2.3 — Inconsistent bot reference handling in `send_long_message` ✅ Fixed
**Problem**: Lines 72-75 use `hasattr` checks to handle both `bot` and `context` objects being passed.

**Fix applied**: Function signature now only accepts a `Bot` object. Removed the `hasattr` branching — just calls `bot.send_message()` directly.

---

### 2.4 — `_make_on_update` stores duplicate messages ✅ Fixed
**Problem**: The `on_update` callback stores the full text of every system notification (turn updates, status messages) to the conversation store. This pollutes the message log with internal status messages.

**Fix applied**: Removed the `store.add_message()` call from the `on_update` closure. System notifications are no longer stored as conversation messages. Meaningful agent messages are stored in `AgentManager` where there is proper agent context.

---

## Category 3: 🟠 ERROR HANDLING (High Priority)

### 3.1 — `load_project` doesn't handle corrupt JSON ✅ Fixed
**Problem**: `conversation_store.py` `load_project()` doesn't catch `json.JSONDecodeError`, but `list_projects()` does. A corrupt file will crash the bot.

**Fix applied**: Wrapped `json.load()` in `load_project()` with try/except for `json.JSONDecodeError` and `OSError`, logs a warning, and returns `None`.

---

### 3.2 — No input validation in /new flow ✅ Fixed
**Problem**: Users can enter empty strings, extremely long names, or paths with special characters.

**Fix applied**:
- `new_name()`: Rejects empty names, names > 50 chars, names with characters other than alphanumeric/spaces/hyphens/underscores.
- `new_directory()`: Verifies the parent directory exists and is writable.

---

### 3.3 — Race conditions with global mutable state 📝 Documented
**Problem**: `active_sessions` and `current_project` are plain dicts accessed from concurrent handlers (since `concurrent_updates=True`). Two users could modify these simultaneously.

**Status**: Known limitation. Proper fix requires `asyncio.Lock` for access, which is a larger refactor. Comment added in `bot.py`.

---

## Category 4: 🟡 ARCHITECTURE (Medium Priority — To Do)

### 4.1 — All state lost on restart
`active_sessions` is in-memory. Bot restart = all running sessions gone with no recovery. Should persist session state and restore on startup.

### 4.2 — O(n) message appending in ConversationStore
`add_message()` reads the entire JSON file, appends one message, and writes the entire file back. For long conversations, this becomes slow. Should use append-only log or a lightweight DB (SQLite).

### 4.3 — `bot.py` is 772 lines with mixed concerns
Handler registration, business logic, project initialization, and message routing are all in one file. Should split into `handlers/`, `services/`, etc.

### 4.4 — Unused streaming implementation
`send_message_streaming()` in `claude_agent.py` is fully implemented but never called. Either use it for real-time output to users, or remove dead code.

### 4.5 — No graceful shutdown
When the bot process exits, running Claude CLI subprocesses may become orphaned. Should register signal handlers to clean up.

---

## Category 5: 🟡 MISSING FEATURES (Medium Priority — To Do)

### 5.1 — No `/delete` command to remove projects from the store

### 5.2 — No `/cancel` command documented in help text
It exists as a fallback in the `/new` ConversationHandler but isn't listed in `/help`.

### 5.3 — No rate limiting to prevent abuse

### 5.4 — No health check endpoint for Docker monitoring

### 5.5 — No conversation export
e.g., `/export` to get conversation as a text file.

### 5.6 — No way to list agents in current project
e.g., `/agents` command.

---

## Category 6: 🟡 CONFIGURATION (Medium Priority — To Do)

### 6.1 — Hardcoded personal paths
`PREDEFINED_PROJECTS` and default `STORE_DIR` use personal paths (`~/Downloads/telegram-claude-bot/data`). Should be fully env-var driven.

### 6.2 — `docker-compose.yml` maps personal paths
Maps personal `~/Downloads` paths. Should use env vars or be documented as "customize before use."

### 6.3 — `.env.example` defaults don't match `config.py` defaults
- `MAX_TURNS_PER_CYCLE`: .env.example says 20, config.py defaults to 50
- `MAX_BUDGET_USD`: .env.example says 5, config.py defaulted to 10000 (now fixed to 5)

---

## Category 7: 🟡 TESTING (Medium Priority — To Do)

### 7.1 — Minimal test coverage
Only 3 unit tests covering `ConversationStore`. No tests for `bot.py`, `claude_agent.py`, or `agent_manager.py`.

### 7.2 — No mocking infrastructure
No mocking infrastructure for Claude CLI subprocess.

### 7.3 — `pytest-asyncio` is installed but not used
Should add async tests for `AgentManager`.

### 7.4 — `test_simulation.py` is a manual integration test
Not discoverable by pytest.

---

## Category 8: 🟢 DOCKER/DEPLOYMENT (Low Priority — To Do)

### 8.1 — Dockerfile doesn't install Claude CLI binary
Container won't work without it.

### 8.2 — No `HEALTHCHECK` instruction in Dockerfile

### 8.3 — No production logging configuration
No log rotation, structured JSON logging, etc.
