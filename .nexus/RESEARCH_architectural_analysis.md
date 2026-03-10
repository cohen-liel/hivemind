# Nexus Agent OS — Deep Architectural Analysis Report

**Task ID:** task_001
**Date:** 2026-03-10
**Scope:** Full codebase analysis — backend brain, API/data layer, contracts/config, frontend
**Total Source Lines Analyzed:** ~20,099 Python + ~5,500 TypeScript/TSX

---

## Executive Summary

The Nexus Agent OS is a sophisticated multi-agent orchestration platform with a React frontend and Python/FastAPI backend. The codebase demonstrates strong architectural vision but has accumulated significant technical debt, particularly in:

1. **Monolithic files** — `orchestrator.py` (4,884 lines) is the single largest risk; 3 functions alone consume 1,331 lines (27% of the file)
2. **Concurrency safety** — Race conditions exist in state.py, session_manager.py, events.py, and sdk_client.py
3. **Error handling** — Pervasive `except Exception: pass` patterns silently swallow errors across all layers
4. **Frontend performance** — Missing memoization, no virtualization for large lists, 1-second `now` tick causes cascading re-renders

The report below contains **87 unique findings** across **19 files**, prioritized by severity.

---

## Table of Contents

1. [Critical Findings (Must Fix)](#1-critical-findings)
2. [High Priority Findings](#2-high-priority-findings)
3. [Medium Priority Findings](#3-medium-priority-findings)
4. [Low Priority Findings](#4-low-priority-findings)
5. [Module-by-Module Summary](#5-module-by-module-summary)
6. [Recommended Action Plan](#6-recommended-action-plan)

---

## 1. Critical Findings

### C-01: orchestrator.py — Monolithic Function `_orch_heartbeat()` (640 lines)
- **File:** `orchestrator.py`, lines 2054–2693
- **Description:** Single nested async function containing the entire orchestration loop: heartbeat/status polling, orchestrator query execution, response parsing, agent delegation, stuck detection, role parallel execution, review prompt building, and sub-agent task execution. Impossible to unit-test individual behaviors.
- **Impact:** Untestable, unmaintainable, high risk of regression on any change
- **Fix:** Extract into 4–5 focused methods: `_execute_orchestrator_query()`, `_process_delegations()`, `_execute_agent_roles()`, `_handle_orchestrator_response()`

### C-02: orchestrator.py — Monolithic Function `_build_review_prompt()` (429 lines)
- **File:** `orchestrator.py`, lines 3900–4328
- **Description:** Single method handles entire agent output analysis pipeline: budget/round calculation, output parsing and classification, finding extraction (issues, test results, files), crash detection, and delegate block generation.
- **Impact:** Any change to review logic risks breaking prompt construction; extremely hard to debug
- **Fix:** Extract into `_parse_agent_outputs()`, `_extract_findings()`, `_classify_agent_status()`, `_build_delegate_blocks()`

### C-03: dag_executor.py — Function Name Collision `_validate_artifacts()`
- **File:** `dag_executor.py`, lines 719 AND 1030
- **Description:** Two different functions share the name `_validate_artifacts()` but have different signatures and purposes. The second definition (line 1030) shadows the first (line 719). Line 309 calls the second version; line 670 calls the first.
- **Impact:** Python resolves to the second definition — the first version is dead code. Artifact validation at line 670 silently uses wrong function.
- **Fix:** Rename second function to `_check_required_artifact_types()` and verify all call sites

### C-04: state.py — No Lock in `get_manager()` / `get_all_managers()`
- **File:** `state.py`, lines 69–93 (get_manager), lines 96–111 (get_all_managers)
- **Description:** These synchronous functions read `active_sessions` dict without acquiring `_state_lock`, while `register_manager()` and `unregister_manager()` are async and DO acquire the lock. Dict can be modified during iteration.
- **Impact:** Stale reads, potential RuntimeError from dict size change during iteration
- **Fix:** Make both functions async and acquire `_state_lock`; or use `threading.Lock` for sync access

### C-05: state.py — Initialization Race Condition
- **File:** `state.py`, lines 51–64
- **Description:** `initialize()` checks `sdk_client is None` and `session_mgr is None` without any lock. Two concurrent calls both see `None`, both create instances, second overwrites first. First instance leaks (DB connections, threads).
- **Impact:** Resource leak, duplicate initialization, unpredictable behavior
- **Fix:** Add initialization lock or `_initialized` flag guarded by `_state_lock`

### C-06: session_manager.py — Connection Pool Deadlock Risk
- **File:** `session_manager.py`, lines 374–377
- **Description:** Health check executes `SELECT 1` with 2s timeout on acquired connection. If connection hangs, timeout fires and a new connection is created (line 383). Old connection never closed until next health check. With 5-connection max, repeated health check failures exhaust the pool.
- **Impact:** Complete database unavailability under connection instability
- **Fix:** Close failed health-check connections immediately; add connection age tracking

### C-07: config.py — AUTH_ENABLED Boolean Check Unsafe
- **File:** `config.py`, line 174
- **Description:** `AUTH_ENABLED: bool = bool(os.getenv("DASHBOARD_API_KEY", ""))` — Any non-empty string evaluates to True. Setting `DASHBOARD_API_KEY="0"` or `DASHBOARD_API_KEY="false"` enables auth with that literal string as the key.
- **Impact:** Security misconfiguration; auth bypassed or enabled unexpectedly
- **Fix:** Use explicit parsing: `os.getenv("AUTH_ENABLED", "false").lower() in ("1", "true", "yes")`

---

## 2. High Priority Findings

### H-01: orchestrator.py — Distributed State (9+ Attributes)
- **File:** `orchestrator.py`, lines 125–293
- **Description:** Execution state scattered across `agent_states`, `_completed_rounds`, `_agents_used`, `shared_context`, `_dag_task_statuses`, `_current_dag_graph`, `conversation_log`, `total_cost_usd`, `_current_loop`. No single source of truth; checkpoint/restore logic (lines 335–370) must manually handle all attributes.
- **Fix:** Consolidate into an `ExecutionState` dataclass with serialization methods

### H-02: dag_executor.py — `execute_graph()` Too Long (266 lines)
- **File:** `dag_executor.py`, lines 139–405
- **Description:** Main entry point combining orchestration loop, batch planning, failure handling, auto-commit, and result aggregation.
- **Fix:** Split into `_execute_batch_round()`, `_check_and_heal_failures()`, `_process_batch_results()`

### H-03: dag_executor.py — `_run_single_task()` Too Long (292 lines)
- **File:** `dag_executor.py`, lines 427–719
- **Description:** Two-phase execution combining context gathering, prompt building, work phase, summary phase, validation, and error classification.
- **Fix:** Split into `_prepare_task_context()`, `_run_work_phase()`, `_finalize_task_output()`

### H-04: dag_executor.py — Path Traversal in Artifact Validation
- **File:** `dag_executor.py`, lines 726–735
- **Description:** `artifact_path.lstrip("/")` is incomplete — `//etc/passwd` bypasses it. No `Path.resolve()` validation against project directory.
- **Fix:** Use `(project_path / artifact_path).resolve()` and verify it starts with `project_path.resolve()`

### H-05: dag_executor.py — Mutable Graph Modified During Execution
- **File:** `dag_executor.py`, line 969
- **Description:** `ctx.graph.add_task(remediation)` modifies the live graph while other tasks may be executing concurrently. No lock protection on `ctx.graph.tasks`.
- **Fix:** Add asyncio.Lock for graph mutations or use copy-on-write pattern

### H-06: memory_agent.py — Snapshot Mutation Without Locking
- **File:** `memory_agent.py`, lines 336–344
- **Description:** `update_project_memory()` is async but heuristic updates mutate snapshot fields without any lock. Concurrent calls for same project cause data corruption.
- **Fix:** Use file-level lock per project_id or state.py's `_state_lock`

### H-07: memory_agent.py — Non-Atomic File Writes
- **File:** `memory_agent.py`, lines 451–457, 512, 533, 581
- **Description:** `Path.write_text()` is not atomic. Concurrent `update_project_memory()` calls can interleave writes, producing corrupted JSON in `memory_snapshot.json`.
- **Fix:** Use atomic write-then-rename pattern: write to `.tmp` file, then `os.rename()`

### H-08: state.py — `current_project` State Orphaning
- **File:** `state.py`, lines 30–31, 141–156
- **Description:** `current_project[user_id]` can point to a project_id that has been unregistered. `unregister_manager()` doesn't check or clean up `current_project`.
- **Fix:** Add cascade cleanup in `unregister_manager()`: `if current_project.get(user_id) == project_id: del current_project[user_id]`

### H-09: events.py — Sequence Counter Not Atomic
- **File:** `dashboard/events.py`, lines 468–472
- **Description:** `_next_sequence()` reads, increments, and writes counter without lock. Two concurrent `publish()` calls could assign same sequence_id.
- **Fix:** Use `asyncio.Lock` or `itertools.count()` for atomic sequence generation

### H-10: session_manager.py — Pool Size Counter Not Atomic
- **File:** `session_manager.py`, lines 366–367, 383
- **Description:** `self._size += 1` not protected by lock. Multiple coroutines can both increment, exceeding `max_connections`.
- **Fix:** Move size increment inside the lock section

### H-11: dashboard/api.py — Manager Creation Race Condition
- **File:** `dashboard/api.py`, lines 1243–1262
- **Description:** `_find_manager(project_id)` checks active managers, then if not found, loads from DB and creates new manager — without lock between check and create. Two concurrent requests create duplicate managers.
- **Fix:** Add async lock per project_id for manager creation

### H-12: sdk_client.py — Circuit Breaker Half-Open Race
- **File:** `sdk_client.py`, lines 433–437
- **Description:** Sets `_half_open_in_flight = True` to allow one probe request. But two async tasks can both see `False` and both start probes simultaneously.
- **Fix:** Use `asyncio.Lock` around half-open state transition

### H-13: contracts.py — `extract_task_output()` Monolithic (193 lines)
- **File:** `contracts.py`, lines 933–1125
- **Description:** Single function with 7 inline signal detection methods, file path regex, action verb matching, and confidence scoring. Extremely hard to test or modify.
- **Fix:** Extract into `WorkSignalDetector` class with separate methods per signal type

### H-14: config.py — PROJECTS_BASE_DIR Silent Failure
- **File:** `config.py`, lines 73–77
- **Description:** Directory creation catches `OSError` with `pass`. Later code using `PROJECTS_BASE_DIR` fails with cryptic "directory not found" errors.
- **Fix:** Raise `ConfigError` if directory cannot be created; or log ERROR and exit

### H-15: Frontend — Silent `.catch(() => {})` Error Swallowing
- **Files:** `ProjectView.tsx` line 159, `useProjectActions.ts` lines 77/87/98/109/127/139, `WebSocketContext.tsx` lines 60/76/140
- **Description:** At least 12 Promise `.catch(() => {})` calls silently swallow errors. Users never see failure feedback; developers can't diagnose issues from logs.
- **Fix:** Replace with proper error handling: set error state, show toast, or log to monitoring

### H-16: Frontend — Missing Activity Feed Virtualization
- **File:** `frontend/src/components/ActivityFeed.tsx`
- **Description:** Large activity feeds render all items without virtualization. At 1000+ entries, DOM becomes sluggish.
- **Fix:** Use `react-virtuoso` or `react-window` for windowed rendering

---

## 3. Medium Priority Findings

### M-01: orchestrator.py — Silent Exception Handlers
- **File:** `orchestrator.py`, lines 1044, 767, 774, 3318
- **Description:** Multiple `except Exception: pass` blocks swallow errors without logging. Makes debugging nearly impossible.
- **Fix:** Add `logger.debug()` at minimum for all caught exceptions

### M-02: orchestrator.py — `conversation_log` Uses List Instead of Deque
- **File:** `orchestrator.py`, line 3823
- **Description:** `self.conversation_log = self.conversation_log[-2000:]` creates new list every time (O(n)). Called frequently.
- **Fix:** Use `collections.deque(maxlen=2000)` for O(1) bounded appends

### M-03: orchestrator.py — 27+ Repetitive Event Emission Calls
- **File:** `orchestrator.py`, throughout
- **Description:** Same `await self._emit_event("project_status", ...)` pattern repeated 27+ times with slight variations.
- **Fix:** Create wrapper methods: `_emit_agent_update()`, `_emit_project_status()`, `_emit_agent_finished()`

### M-04: dag_executor.py — Bare Exception Handlers (6 locations)
- **File:** `dag_executor.py`, lines 300, 317, 461, 511, 517, 836
- **Description:** Silent `except Exception` blocks swallow callback failures, auto-commit errors, and streaming errors.
- **Fix:** Log all exceptions; distinguish recoverable vs. fatal errors

### M-05: dag_executor.py — Race Condition on `ctx.task_counter`
- **File:** `dag_executor.py`, line 966
- **Description:** `ctx.task_counter += 1` is not atomic — concurrent calls could produce duplicate task IDs.
- **Fix:** Use `asyncio.Lock` or `itertools.count()`

### M-06: pm_agent.py — System Prompt XML Injection
- **File:** `pm_agent.py`, line 289
- **Description:** `f"<user_request>{user_message}</user_request>"` — if `user_message` contains `</user_request>`, XML structure is broken.
- **Fix:** XML-escape user inputs before interpolation

### M-07: pm_agent.py — JSON Parsing Without Size Limits
- **File:** `pm_agent.py`, lines 345–347
- **Description:** `json.loads(candidate)` with no size validation. Malicious PM response could crash parser.
- **Fix:** Add size check: `if len(candidate) > MAX_JSON_SIZE: continue`

### M-08: pm_agent.py — Prompt Constants Are 35% of File
- **File:** `pm_agent.py`, lines 46–204
- **Description:** 158 lines of prompt template string concatenation inside the module.
- **Fix:** Move to `prompts/pm_prompts.py` or YAML configuration

### M-09: pm_agent.py — Global SDK Client Dependency
- **File:** `pm_agent.py`, lines 232–234; also `dag_executor.py`, `memory_agent.py`
- **Description:** All three files access `state.sdk_client` global. No dependency injection; makes unit testing require monkeypatching.
- **Fix:** Accept SDK as parameter in public functions

### M-10: memory_agent.py — Read-Modify-Write Race on Decision Log
- **File:** `memory_agent.py`, lines 551–561
- **Description:** File rotation reads entire file, splits, writes two files. Another thread could modify log between read and write.
- **Fix:** Use file lock (fcntl) during rotation

### M-11: memory_agent.py — Unbounded `key_decisions` Growth
- **File:** `memory_agent.py`, line 339
- **Description:** `snapshot.key_decisions = snapshot.key_decisions + new_decisions` — append-only design; list grows indefinitely across sessions.
- **Fix:** Cap at configurable max (e.g., 100 decisions) with oldest eviction

### M-12: contracts.py — Missing Max-Length on TaskOutput Lists
- **File:** `contracts.py`, lines 266–268
- **Description:** `issues`, `blockers`, `followups` lists have no max_length constraint. Could accumulate unbounded entries.
- **Fix:** Add `max_length=50` Pydantic validator

### M-13: contracts.py — TaskOutput.confidence Default 1.0 Contradicts Code
- **File:** `contracts.py`, line 271 vs. line 1090
- **Description:** Default confidence is 1.0 (100%), but `extract_task_output()` caps inferred work at 0.85. Contradictory.
- **Fix:** Change default to 0.5 or None (require explicit setting)

### M-14: contracts.py — `api_surface` Uses Untyped Dict
- **File:** `contracts.py`, lines 328–330
- **Description:** `api_surface: list[dict[str, str]]` — should be `list[ApiEndpoint]` with a typed model.
- **Fix:** Create `ApiEndpoint(BaseModel)` with `method`, `path`, `description` fields

### M-15: config.py — 730 Lines of Prompts (64% of File)
- **File:** `config.py`, lines 333–1150
- **Description:** Prompt templates consume 64% of the configuration module. Makes the file hard to navigate and maintain.
- **Fix:** Extract to `prompts/` directory with separate files per agent type

### M-16: config.py — Prompt Footer Duplicated 15× in Memory
- **File:** `config.py`, lines 750–1150
- **Description:** `_TYPED_CONTRACT_FOOTER` (~150 lines) concatenated 15 times into different specialist prompts. ~250KB of redundant memory.
- **Fix:** Use lazy loading or string templating to share single footer instance

### M-17: dashboard/api.py — Weak CORS Configuration
- **File:** `dashboard/api.py`, lines 289–302
- **Description:** `allow_methods=["*"]` and `allow_headers=["*"]` are overly permissive. Default origins include localhost URLs.
- **Fix:** Restrict to specific methods/headers; require explicit CORS_ORIGINS in production

### M-18: dashboard/api.py — Rate Limit Memory Leak
- **File:** `dashboard/api.py`, lines 339–380
- **Description:** In-memory rate limit store cleaned only every 500 requests or at 500 entries. Between cleanups, memory grows linearly with unique IPs.
- **Fix:** Use time-based cleanup on every request or switch to sliding window counter

### M-19: events.py — Ring Buffer Dict Never Cleaned Up
- **File:** `dashboard/events.py`, lines 474–478
- **Description:** `_ring_buffers` dict grows with each new project. No eviction of old/dormant project ring buffers.
- **Fix:** Add TTL-based eviction or max projects cap

### M-20: session_manager.py — Schema Migration Catches All Exceptions
- **File:** `session_manager.py`, lines 1210–1240
- **Description:** Tries to add columns, catches Exception to detect "already exists". PermissionError, disk full, etc. also silently ignored.
- **Fix:** Catch specific `OperationalError` or check column existence first

### M-21: session_manager.py — Activity Log Cleanup Not Transactional
- **File:** `session_manager.py`, lines 1083–1095
- **Description:** Deletes old rows in loop without transaction. Crash between deletes leaves orphaned data.
- **Fix:** Wrap in explicit transaction

### M-22: Frontend — `now` State Causes Cascading Re-renders
- **File:** `frontend/src/pages/ProjectView.tsx`, line 70
- **Description:** `setNow(Date.now())` every 1 second causes full re-render cascade through all child components using `now` prop.
- **Fix:** Use `useRef` for time tracking where possible; memoize children with `React.memo`

### M-23: Frontend — Dashboard Missing useMemo for Computed Values
- **File:** `frontend/src/pages/Dashboard.tsx`, lines 141–197
- **Description:** `filteredProjects`, `runningCount`, and `statusConfig` recomputed on every render. `statusConfig` function recreated each render.
- **Fix:** Wrap in `useMemo`/`useCallback` with appropriate dependencies

### M-24: Frontend — WebSocketContext Mutable State Outside React
- **File:** `frontend/src/WebSocketContext.tsx`, line 24
- **Description:** `_projectSequences` is a mutable object outside React component tree. Modifications don't trigger re-renders; state can get out of sync.
- **Fix:** Move to `useRef` or integrate into context state

### M-25: Frontend — SchedulesPage 9 useState Hooks
- **File:** `frontend/src/pages/SchedulesPage.tsx`, lines 9–21
- **Description:** 9 separate `useState` hooks for form management. Hard to reason about state transitions.
- **Fix:** Consolidate into `useReducer` (similar to ProjectView.tsx pattern)

### M-26: Frontend — Weak TypeScript Typing
- **Files:** `SettingsPage.tsx` line 62, `ProjectView.tsx` line 180
- **Description:** Uses `as unknown as Record<string, unknown>` and `as AgentStateType['state']` forced casts indicating incomplete type definitions.
- **Fix:** Define proper types for settings response and agent state events

### M-27: Frontend — Missing Modal Focus Traps
- **File:** `frontend/src/components/ApprovalModal.tsx`, line 22
- **Description:** Modal has `tabIndex={-1}` but no focus trap. Keyboard users can tab outside the modal.
- **Fix:** Implement focus trap (e.g., `focus-trap-react` library or manual implementation)

---

## 4. Low Priority Findings

### L-01: orchestrator.py — Nested Functions Prevent Unit Testing
- **File:** `orchestrator.py`, lines 2054, 2819, 3083
- **Description:** Key logic lives in nested functions inside `_run_orchestrator()`. Cannot be imported or tested independently.
- **Fix:** Promote to class methods

### L-02: orchestrator.py — Context Token Trimming O(n log n)
- **File:** `orchestrator.py`, lines 3447–3526
- **Description:** Priority-based sorting of all context entries each time budget exceeded. On EPIC tasks with 100+ entries, becomes expensive.
- **Fix:** Use incremental trimming or priority heap

### L-03: dag_executor.py — `build_execution_summary()` Possibly Unused
- **File:** `dag_executor.py`, lines 1118–1161
- **Description:** Function defined but no internal callers found. May be dead code.
- **Fix:** Verify external usage; remove if unused

### L-04: pm_agent.py — Role Default Artifacts Duplicates Contracts
- **File:** `pm_agent.py`, lines 375–384
- **Description:** `_ROLE_DEFAULT_ARTIFACTS` dict hardcodes role→artifact mapping, duplicating information that should come from `AgentRole` or `contracts.py`.
- **Fix:** Derive from contracts or centralize mapping

### L-05: memory_agent.py — JSON Serialization Waste
- **File:** `memory_agent.py`, line 230
- **Description:** `json.dumps(art.data, indent=2)[:800]` — serializes entire artifact data even when truncating to 800 chars.
- **Fix:** Use `json.dumps()` without indent for truncation, or limit data size before serialization

### L-06: contracts.py — Regex Patterns Compiled at Runtime
- **File:** `contracts.py`, lines 1004–1007
- **Description:** `re.findall()` patterns in `extract_task_output()` compiled on every call.
- **Fix:** Pre-compile as module-level constants

### L-07: config.py — Inconsistent Case in `_get()` Helper
- **File:** `config.py`, lines 55–58
- **Description:** `key.lower()` for override lookup but `os.getenv(key)` uses original case. On Unix, env vars are case-sensitive.
- **Fix:** Standardize to uppercase for both lookups

### L-08: Frontend — `useDagPersistence` No Storage Quota Handling
- **File:** `frontend/src/hooks/useDagPersistence.ts`, line 49
- **Description:** `localStorage.setItem` can throw `QuotaExceededError`. Not caught.
- **Fix:** Wrap in try-catch with graceful degradation

### L-09: Frontend — No Rate Limiting on Message Send
- **File:** `frontend/src/components/Controls.tsx`
- **Description:** Send button allows rapid-fire submissions without debounce or rate limiting.
- **Fix:** Add debounce or disable button for 1s after send

### L-10: Frontend — Toast System Unbounded
- **File:** `frontend/src/components/Toast.tsx`
- **Description:** No limit on concurrent toasts. Rapid errors could spam the UI.
- **Fix:** Add max visible toasts (e.g., 5) with queue

---

## 5. Module-by-Module Summary

| Module | Lines | Critical | High | Medium | Low | Top Issue |
|--------|-------|----------|------|--------|-----|-----------|
| `orchestrator.py` | 4,884 | 2 | 1 | 3 | 2 | 640-line monolithic function |
| `dag_executor.py` | 1,161 | 1 | 3 | 2 | 1 | Function name collision bug |
| `pm_agent.py` | 448 | 0 | 0 | 4 | 1 | XML injection, no DI |
| `memory_agent.py` | 582 | 0 | 2 | 2 | 1 | Non-atomic file writes |
| `state.py` | 171 | 2 | 1 | 0 | 0 | Thread safety gaps |
| `contracts.py` | 1,326 | 0 | 1 | 3 | 1 | 193-line extract function |
| `config.py` | 1,162 | 1 | 1 | 2 | 1 | Auth bypass, prompt bloat |
| `dashboard/api.py` | 1,870 | 0 | 1 | 2 | 0 | Manager creation race |
| `dashboard/events.py` | 728 | 0 | 1 | 1 | 0 | Sequence ID not atomic |
| `session_manager.py` | 2,025 | 1 | 1 | 2 | 0 | Pool deadlock risk |
| `sdk_client.py` | 1,425 | 0 | 1 | 0 | 0 | Circuit breaker race |
| `server.py` | 382 | 0 | 0 | 0 | 0 | Clean (minor issues) |
| Frontend (all) | ~5,500 | 0 | 2 | 6 | 3 | Silent errors, no virtualization |
| **TOTAL** | ~21,664 | **7** | **15** | **27** | **10** | |

---

## 6. Recommended Action Plan

### Phase 1: Safety & Correctness (Weeks 1–2)
1. **Fix C-03:** Rename `_validate_artifacts()` collision in dag_executor.py
2. **Fix C-04 + C-05:** Add proper locking to state.py (`get_manager`, `initialize`)
3. **Fix C-07:** Fix AUTH_ENABLED boolean parsing in config.py
4. **Fix C-06:** Fix session_manager.py pool deadlock
5. **Fix H-04:** Add path traversal protection in dag_executor.py
6. **Fix H-09:** Make events.py sequence counter atomic

### Phase 2: Error Handling Hardening (Week 3)
7. **Fix M-01 + M-04:** Replace all `except Exception: pass` with proper logging (orchestrator.py, dag_executor.py)
8. **Fix H-15:** Replace frontend `.catch(() => {})` with error handling and user feedback
9. **Fix M-20 + M-21:** Fix session_manager.py exception handling and transactions

### Phase 3: Monolith Splitting (Weeks 4–6)
10. **Fix C-01:** Extract `_orch_heartbeat()` into focused methods
11. **Fix C-02:** Extract `_build_review_prompt()` helpers
12. **Fix H-02 + H-03:** Split dag_executor.py long functions
13. **Fix H-13:** Extract `extract_task_output()` into class
14. **Fix M-15:** Move prompts out of config.py into `prompts/` directory
15. **Fix M-08:** Move PM prompts out of pm_agent.py

### Phase 4: State & Performance (Weeks 7–8)
16. **Fix H-01:** Consolidate orchestrator state into `ExecutionState` dataclass
17. **Fix H-06 + H-07:** Add locking and atomic writes to memory_agent.py
18. **Fix M-22 + M-23:** Fix frontend re-render performance (useMemo, useRef for `now`)
19. **Fix H-16:** Add activity feed virtualization

### Phase 5: Code Quality (Weeks 9–10)
20. **Fix M-09:** Introduce dependency injection for SDK client across all modules
21. **Fix M-14 + M-26:** Improve type safety in contracts.py and frontend
22. **Fix M-27:** Add accessibility improvements (focus traps, ARIA labels)
23. **Fix L-01:** Promote nested functions to class methods in orchestrator.py

---

## Appendix: Files Requiring Changes (by priority)

| Priority | File | Changes Needed |
|----------|------|----------------|
| P0 | `state.py` | Add locking, fix initialization race |
| P0 | `dag_executor.py` | Fix name collision, path traversal |
| P0 | `config.py` | Fix AUTH_ENABLED, validate PROJECTS_BASE_DIR |
| P0 | `session_manager.py` | Fix pool deadlock, atomic pool size |
| P1 | `orchestrator.py` | Split 3 monolithic functions, fix error handling |
| P1 | `dashboard/events.py` | Atomic sequence counter, ring buffer cleanup |
| P1 | `dashboard/api.py` | Manager creation lock, CORS hardening |
| P1 | `sdk_client.py` | Fix circuit breaker race |
| P1 | `contracts.py` | Split extract function, add validations |
| P2 | `memory_agent.py` | Atomic writes, file locking, bounded lists |
| P2 | `pm_agent.py` | XML escaping, size limits, DI |
| P2 | `config.py` | Extract prompts to separate module |
| P2 | Frontend (all) | Error handling, memoization, virtualization |
