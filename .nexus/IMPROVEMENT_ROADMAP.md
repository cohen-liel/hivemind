# Improvement Roadmap — Web Claude Bot

**Date**: 2026-03-08 | **Author**: Researcher Agent | **Depth**: Deep (Tier 3)
**Scope**: Performance · Reliability · Observability · DX · Deployment

> This roadmap complements `.nexus/IMPROVEMENT_PLAN.md` (which tracks bugs and security fixes).
> Here we focus on **architectural improvements** that harden the system for daily use.

---

## Executive Summary

The web-claude-bot is functionally complete: multi-agent orchestration works, WebSocket streaming delivers real-time updates, SQLite persistence is solid, and Docker deployment is ready. The codebase has already been through a thorough bug-fix and security-hardening pass (22 items fixed, 131 tests passing).

**What's missing is production resilience.** The app runs on a single aiosqlite connection with no caching, spawns Claude CLI subprocesses with minimal lifecycle management, uses unstructured stdout logging, and has no circuit breakers or graceful degradation. The frontend lacks code splitting and optimistic updates. These are the gaps between "it works" and "it works reliably under real-world conditions."

This roadmap provides **42 specific improvements** across 5 categories, each with an impact/effort score and a concrete implementation plan. Items are ordered within each category by impact-adjusted priority (high impact + low effort first).

---

## Impact/Effort Scoring

| Score | Impact | Effort |
|-------|--------|--------|
| 🟢 | High — directly improves user experience or prevents failures | Low — <1 hour, single file |
| 🟡 | Medium — measurable improvement for power users | Medium — 1-3 hours, 2-4 files |
| 🔴 | Low — polish, future-proofing | High — 3+ hours, architectural change |

**Priority formula**: `Priority = Impact ÷ Effort`. Items within each tier are already sorted by this ratio.

---

## 1. Performance Optimizations

### Current State Assessment

| Area | Current Pattern | Best Practice | Gap |
|------|----------------|---------------|-----|
| DB connections | Single `aiosqlite.Connection` for all ops | Read/write separation or small pool | Contention under concurrent writes |
| Caching | None — every request hits DB | In-memory cache for hot data (project lists, settings) | Unnecessary DB round-trips |
| File I/O | Synchronous `Path.read_text()`, `Path.iterdir()` in async context | `asyncio.to_thread()` or `aiofiles` for all I/O | Blocks event loop |
| Git operations | 3-4 `git` subprocesses per sub-agent round | Cached results with 10s TTL | Redundant subprocess spawning |
| Frontend bundle | No code splitting, no lazy loading | `React.lazy()` + `Suspense` for routes | Entire app loaded upfront |
| SQLite PRAGMAs | WAL mode only | WAL + `synchronous=NORMAL` + `cache_size` + `mmap_size` | Sub-optimal read perf |

### P-01 · SQLite PRAGMA Tuning 🟢Impact 🟢Effort

**What**: Add performance-critical PRAGMAs after enabling WAL mode in `SessionManager.initialize()`.

**Why**: SQLite defaults are conservative. The `synchronous=NORMAL` setting is safe with WAL mode and reduces fsync calls by ~50%. `mmap_size` enables memory-mapped I/O for reads, and `cache_size` keeps more pages in memory.

**Current** (`session_manager.py:203`):
```python
await self._db.execute("PRAGMA journal_mode=WAL")
```

**Target**:
```python
await self._db.execute("PRAGMA journal_mode=WAL")
await self._db.execute("PRAGMA synchronous=NORMAL")    # Safe with WAL, 2x faster writes
await self._db.execute("PRAGMA cache_size=-8000")       # 8MB page cache (default 2MB)
await self._db.execute("PRAGMA mmap_size=67108864")     # 64MB mmap for reads
await self._db.execute("PRAGMA journal_size_limit=67108864")  # Cap WAL at 64MB
await self._db.execute("PRAGMA temp_store=MEMORY")      # Temp tables in memory
await self._db.execute("PRAGMA busy_timeout=5000")       # 5s wait on lock instead of immediate fail
```

**Evidence**: SQLite documentation confirms `synchronous=NORMAL` with WAL provides durability guarantees equivalent to full sync in rollback mode. Ben Johnson (Litestream author) and the SQLite team recommend this combination for production use.

**Files**: `session_manager.py`

---

### P-02 · In-Memory Cache for Hot Data 🟢Impact 🟡Effort

**What**: Add a TTL-based cache for frequently-read, rarely-changed data: project list, project details, settings.

**Why**: `list_projects()` is called on every frontend page load (and on the 10s poll fallback). `load_project()` is called on every API request that needs project context. These queries hit SQLite every time despite data changing only on user actions.

**Implementation**:
```python
# cache.py — Simple TTL cache (no dependency needed)
import time
from typing import Any

class TTLCache:
    """Lightweight async-safe TTL cache for hot data."""

    def __init__(self, default_ttl: float = 5.0):
        self._store: dict[str, tuple[float, Any]] = {}
        self._ttl = default_ttl

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry and time.monotonic() - entry[0] < self._ttl:
            return entry[1]
        return None

    def set(self, key: str, value: Any, ttl: float | None = None):
        self._store[key] = (time.monotonic(), value)

    def invalidate(self, key: str):
        self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str):
        self._store = {k: v for k, v in self._store.items() if not k.startswith(prefix)}

_cache = TTLCache(default_ttl=5.0)
```

**Usage**: Wrap `list_projects()` and `load_project()` with cache lookups. Invalidate on write operations (save_project, update_status, etc.).

**Files**: New `cache.py`, modify `session_manager.py`, `dashboard/api.py`

---

### P-03 · Wrap Blocking I/O with `asyncio.to_thread()` 🟢Impact 🟢Effort

**What**: Wrap all synchronous filesystem operations in async wrappers.

**Why**: `Path.read_text()`, `Path.iterdir()`, `os.path.exists()` block the asyncio event loop. In `orchestrator.py`, `_read_project_manifest()` and `_get_workspace_context()` are called every orchestrator loop iteration. A slow NFS mount or large directory could stall all WebSocket connections and API responses for seconds.

**Current anti-patterns** (orchestrator.py):
```python
# Blocking — stalls entire event loop
content = manifest_path.read_text(encoding="utf-8")
items = list(project_path.iterdir())
```

**Target**:
```python
# Non-blocking — runs in thread pool
content = await asyncio.to_thread(manifest_path.read_text, encoding="utf-8")
items = await asyncio.to_thread(lambda: list(project_path.iterdir()))
```

**Files**: `orchestrator.py` (2 functions), `dashboard/api.py` (`read_file`, `get_file_tree`, `browse_dirs`)

---

### P-04 · Git Operation Caching 🟡Impact 🟡Effort

**What**: Cache git subprocess results per orchestrator round with a 10-second TTL.

**Why**: Each sub-agent completion triggers `git diff --stat`, `git diff HEAD`, and `git log --oneline` in `_accumulate_context()`. Then `_detect_file_changes()` runs again in rejection/nudge handlers. A round with 3 agents spawns ~12 git subprocesses for overlapping data. Each subprocess takes 50-200ms, totaling 0.6-2.4s of blocked time per round.

**Implementation**:
```python
class _GitCache:
    """Per-round cache for git subprocess results."""

    def __init__(self, ttl: float = 10.0):
        self._cache: dict[str, tuple[float, str]] = {}
        self._ttl = ttl

    async def run(self, cmd: list[str], cwd: str) -> str:
        key = f"{cwd}:{' '.join(cmd)}"
        entry = self._cache.get(key)
        if entry and time.monotonic() - entry[0] < self._ttl:
            return entry[1]

        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        result = stdout.decode(errors="replace")
        self._cache[key] = (time.monotonic(), result)
        return result

    def clear(self):
        self._cache.clear()
```

**Files**: `orchestrator.py` — add `_git_cache` attribute to `OrchestratorManager`, clear at loop start

---

### P-05 · Frontend Code Splitting with React.lazy 🟡Impact 🟡Effort

**What**: Lazy-load route components so the initial bundle only contains the project list page.

**Why**: Currently, the entire app (all routes, all components) is loaded in a single JS bundle. The Settings page, Schedule manager, and detailed project view are loaded even when the user just wants to see the project list. React.lazy + Suspense defers these until navigation.

**Implementation** (frontend/src/App.tsx or router):
```tsx
import { lazy, Suspense } from 'react';

const ProjectDetail = lazy(() => import('./pages/ProjectDetail'));
const Settings = lazy(() => import('./pages/Settings'));
const Schedules = lazy(() => import('./pages/Schedules'));

// In router:
<Suspense fallback={<div className="animate-pulse">Loading...</div>}>
  <Route path="/project/:id" element={<ProjectDetail />} />
  <Route path="/settings" element={<Settings />} />
</Suspense>
```

**Also add** to `vite.config.ts`:
```ts
build: {
  rollupOptions: {
    output: {
      manualChunks: {
        vendor: ['react', 'react-dom', 'react-router-dom'],
      },
    },
  },
},
```

**Files**: Frontend `src/App.tsx` (or router file), `vite.config.ts`

---

### P-06 · Read/Write Connection Separation 🟡Impact 🔴Effort

**What**: Use two `aiosqlite` connections: one for reads (SELECT), one for writes (INSERT/UPDATE/DELETE).

**Why**: SQLite with WAL mode supports concurrent readers with a single writer. The current single-connection design serializes all operations. Under load (e.g., 3 agents finishing simultaneously while the frontend polls project status), read queries queue behind write commits. Separating connections lets reads proceed without waiting for write locks.

**Implementation**:
```python
class SessionManager:
    def __init__(self, db_path: str = SESSION_DB_PATH):
        self.db_path = db_path
        self._read_db: aiosqlite.Connection | None = None
        self._write_db: aiosqlite.Connection | None = None

    async def initialize(self):
        self._write_db = await aiosqlite.connect(self.db_path)
        self._read_db = await aiosqlite.connect(self.db_path)
        # Set PRAGMAs on both connections
        for db in (self._write_db, self._read_db):
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
        # Read connection is read-only
        await self._read_db.execute("PRAGMA query_only=ON")
```

**Files**: `session_manager.py` — change `_get_db()` to `_get_read_db()` / `_get_write_db()`

---

### P-07 · Optimistic Frontend Updates 🟡Impact 🟡Effort

**What**: Update UI state immediately on user action, then reconcile with server response.

**Why**: Currently, when a user sends a message, the frontend waits for the WebSocket event before showing any feedback. With Claude CLI spawning taking 2-5 seconds, this creates a perceived lag. Optimistic updates show the user's message immediately in the chat log, with a "pending" indicator that resolves when the server confirms.

**Implementation**: In the message-sending handler:
```tsx
// Immediately add to local state
setMessages(prev => [...prev, {
  agent_name: 'user',
  role: 'user',
  content: message,
  timestamp: Date.now() / 1000,
  pending: true,
}]);

// Send to server
await sendMessage(projectId, message);
// Server confirmation via WebSocket removes "pending" flag
```

**Files**: Frontend message component, WebSocket context

---

### P-08 · Cursor-Based Pagination for Messages 🔴Impact 🟡Effort

**What**: Replace `OFFSET`-based pagination with timestamp-cursor pagination.

**Why**: `get_messages_paginated()` uses `LIMIT ? OFFSET ?`. In SQLite, `OFFSET N` scans and discards N rows before returning results. At offset 10,000, this means reading 10,000 rows to return 50. Cursor-based pagination (`WHERE timestamp < ? ORDER BY timestamp DESC LIMIT ?`) uses the index directly and is O(1) regardless of page depth.

**Current** (`session_manager.py:395`):
```python
"WHERE project_id=? ORDER BY timestamp DESC LIMIT ? OFFSET ?"
```

**Target**:
```python
"WHERE project_id=? AND timestamp < ? ORDER BY timestamp DESC LIMIT ?"
```

**Files**: `session_manager.py`, `dashboard/api.py` (endpoint signature change), frontend (if pagination is used)

---

## 2. Reliability & Resilience

### Current State Assessment

| Area | Current Pattern | Best Practice | Gap |
|------|----------------|---------------|-----|
| Circuit breaking | None — failed CLI calls retry blindly | Circuit breaker pattern for subprocess calls | No backoff after repeated failures |
| Health checks | Single `/api/health` endpoint | Deep health + readiness + liveness separation | No readiness distinction |
| Graceful degradation | Hard failures propagate to frontend | Fallback behaviors for degraded components | All-or-nothing |
| Process lifecycle | `asyncio.create_subprocess_exec` with timeout | Process groups + SIGTERM/SIGKILL escalation | Orphan processes possible |
| WebSocket resilience | Frontend reconnects, no server-side keepalive | Ping/pong heartbeat + connection monitoring | Silent disconnects |
| Error recovery | Retry decorator on DB, retry logic in SDK | Unified error handling strategy | Inconsistent across layers |

### R-01 · Circuit Breaker for Claude CLI Calls 🟢Impact 🟡Effort

**What**: Add a circuit breaker that opens after N consecutive CLI failures, preventing further subprocess spawning until the circuit recovers.

**Why**: If the Claude CLI binary crashes, is uninstalled, or the user's auth expires, every agent delegation will fail after a full timeout period (300s default). With 3 agents per round and 2 retries each, that's 6 × 300s = 30 minutes of wasted waiting. A circuit breaker stops the bleeding after the first 3 failures and returns an immediate error.

**Implementation** (`sdk_client.py`):
```python
from enum import Enum
import time

class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation
    OPEN = "open"            # Failing — reject immediately
    HALF_OPEN = "half_open"  # Testing recovery

class CircuitBreaker:
    """Circuit breaker for Claude CLI subprocess calls."""

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 60.0):
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._last_failure_time = 0.0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time > self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def record_success(self):
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN

    def allow_request(self) -> bool:
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            return True  # Allow one probe request
        return False  # OPEN — reject
```

**Integration**: Check `circuit_breaker.allow_request()` at top of `ClaudeSDKManager.query()`. Record success/failure in finally block. Expose state via `/api/health`.

**Files**: `sdk_client.py`, `dashboard/api.py` (health check enrichment)

---

### R-02 · WebSocket Ping/Pong Heartbeat 🟢Impact 🟢Effort

**What**: Add server-side ping/pong keepalive to detect dead WebSocket connections.

**Why**: Currently, if a client's network drops (laptop sleep, WiFi switch), the server has no way to detect the dead connection. The EventBus subscriber queue fills up silently, consuming memory. WebSocket ping/pong detects dead connections within 30 seconds and allows cleanup.

**Implementation** (in WebSocket handler, `dashboard/api.py`):
```python
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    queue = await event_bus.subscribe()

    async def heartbeat():
        """Send ping every 25s, detect dead connections."""
        while True:
            try:
                await asyncio.sleep(25)
                await ws.send_json({"type": "ping", "timestamp": time.time()})
            except Exception:
                break

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        # ... existing event loop ...
    finally:
        heartbeat_task.cancel()
        await event_bus.unsubscribe(queue)
```

**Note**: Starlette's WebSocket already handles protocol-level ping/pong frames. This adds application-level heartbeats that the frontend can use to show connection status.

**Files**: `dashboard/api.py` (WebSocket handler), frontend WebSocket context (handle ping events)

---

### R-03 · Readiness vs. Liveness Health Checks 🟡Impact 🟢Effort

**What**: Split `/api/health` into three endpoints following Kubernetes patterns.

**Why**: The current health check combines everything into one endpoint. A degraded state (e.g., CLI binary missing but DB works) returns `"degraded"` which is ambiguous for monitoring. Kubernetes, Docker, and monitoring tools expect separate endpoints:

| Endpoint | Purpose | When to fail |
|----------|---------|-------------|
| `/api/health/live` | Is the process alive? | Only on catastrophic failure |
| `/api/health/ready` | Can it serve requests? | DB down, not initialized |
| `/api/health/startup` | Has initialization completed? | Still initializing |

**Implementation**:
```python
@app.get("/api/health/live")
async def liveness():
    return {"status": "ok"}

@app.get("/api/health/ready")
async def readiness():
    db_ok = state.session_mgr and await state.session_mgr.is_healthy()
    sdk_ok = state.sdk_client is not None
    if db_ok and sdk_ok:
        return {"status": "ok"}
    return JSONResponse({"status": "not_ready", "db": db_ok, "sdk": sdk_ok}, status_code=503)

@app.get("/api/health/startup")
async def startup_check():
    if state.session_mgr is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    return {"status": "ok"}
```

**Update Docker HEALTHCHECK**:
```dockerfile
HEALTHCHECK CMD curl -f http://localhost:8080/api/health/ready || exit 1
```

**Files**: `dashboard/api.py`, `Dockerfile`, `docker-compose.yml`

---

### R-04 · Subprocess Lifecycle Management 🟡Impact 🟡Effort

**What**: Use process groups and SIGTERM→SIGKILL escalation for Claude CLI subprocesses.

**Why**: The current `asyncio.wait_for(timeout=AGENT_TIMEOUT_SECONDS)` cancels the Python coroutine but may leave the Claude CLI subprocess running. The subprocess spawns its own child processes (Node.js, potentially other tools). Without process group management, these become orphan processes that consume CPU/memory indefinitely.

**Implementation** (`sdk_client.py`):
```python
import signal

async def _kill_process_tree(proc: asyncio.subprocess.Process, timeout: float = 5.0):
    """Send SIGTERM to process group, escalate to SIGKILL after timeout."""
    if proc.returncode is not None:
        return  # Already exited

    try:
        # Kill entire process group
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)

        # Wait for graceful exit
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            os.killpg(pgid, signal.SIGKILL)
            await proc.wait()
    except (ProcessLookupError, PermissionError):
        pass  # Process already gone
```

**Note**: On macOS, `os.getpgid()` works natively. For Docker/Linux, use `start_new_session=True` when creating the subprocess to put it in its own process group.

**Files**: `sdk_client.py`

---

### R-05 · Graceful Degradation for Missing Components 🟡Impact 🟡Effort

**What**: Continue serving the dashboard in read-only mode when the Claude CLI is unavailable.

**Why**: Currently, if the Claude CLI binary is missing or auth is expired, the entire dashboard is functionally useless — but it still loads, showing no clear indication of the problem. Users can click buttons that fail silently. Graceful degradation means: show project history, show past messages, show agent states — but disable the "Send" button with a clear message explaining why.

**Implementation**:
1. **Backend**: Add a `system_status` field to the project detail response:
```python
@app.get("/api/system/status")
async def system_status():
    cli_available = _check_cli_available()
    circuit_state = sdk_client._circuit_breaker.state.value if sdk_client else "unknown"
    return {
        "cli_available": cli_available,
        "circuit_state": circuit_state,
        "db_available": state.session_mgr is not None,
        "degraded_reason": None if cli_available else "Claude CLI not available",
    }
```

2. **Frontend**: Poll `/api/system/status` on load. If degraded, show a banner and disable action buttons.

**Files**: `dashboard/api.py`, `sdk_client.py`, frontend status component

---

### R-06 · Automatic Session Recovery on Crash 🟡Impact 🔴Effort

**What**: Persist orchestrator state to SQLite so that a server restart can resume in-progress tasks.

**Why**: If the server crashes mid-orchestration (OOM, power loss, accidental Ctrl+C), all in-progress agent work is lost. The user sees the project revert to "idle" with no indication of what happened. Session recovery would reload the conversation log and agent states from the DB and offer to resume.

**Implementation sketch**:
1. Periodically checkpoint orchestrator state (every 5 rounds):
   - `conversation_log` → already in `messages` table
   - `agent_states` → new `orchestrator_state` table
   - `turn_count`, `total_cost_usd` → new columns in `task_history`
2. On startup, check for tasks with `status='running'` in `task_history`
3. Offer to resume via a new API endpoint `/api/projects/{id}/recover`

**Files**: `session_manager.py`, `orchestrator.py`, `server.py`, `dashboard/api.py`

---

### R-07 · Request Timeout Middleware 🟢Impact 🟢Effort

**What**: Add a global timeout for HTTP requests to prevent hung endpoints.

**Why**: If any endpoint hangs (e.g., DB lock, slow subprocess), the client waits indefinitely. A server-side timeout ensures all requests complete within a reasonable window.

**Implementation**:
```python
@app.middleware("http")
async def timeout_middleware(request: Request, call_next):
    try:
        return await asyncio.wait_for(call_next(request), timeout=30.0)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "Request timed out"}, status_code=504)
```

**Files**: `dashboard/api.py`

---

## 3. Observability

### Current State Assessment

| Area | Current Pattern | Best Practice | Gap |
|------|----------------|---------------|-----|
| Logging | `logging.basicConfig()` → stdout, f-string format | Structured JSON logging with context fields | No machine-parseable logs |
| Metrics | `_pool.stats` dict (SDK only) | Counters/gauges for all critical paths | No metrics collection |
| Tracing | `request_id` middleware (HTTP only) | Correlation IDs across WebSocket, SDK calls, orchestrator rounds | Partial tracing |
| Alerting | Budget warning at 80% threshold | Configurable alerts for errors, stalls, budget | Only budget alerts |
| Log rotation | None (stdout only) | RotatingFileHandler or external log management | Unbounded log growth |

### O-01 · Structured Logging with structlog 🟢Impact 🟡Effort

**What**: Replace `logging.basicConfig()` with `structlog` for JSON-structured logs with automatic context binding.

**Why**: Current logs are unstructured text that's impossible to search or aggregate:
```
2026-03-08 14:23:01 - orchestrator - INFO - [req_42315] SDK query OK (12.3s): text_len=1847, cost=$0.0312
```

With structlog, every log entry becomes a JSON object with typed fields:
```json
{"timestamp": "2026-03-08T14:23:01Z", "level": "info", "logger": "orchestrator", "request_id": "req_42315", "event": "sdk_query_complete", "duration_s": 12.3, "text_len": 1847, "cost_usd": 0.0312}
```

**Implementation** (`logging_config.py`):
```python
import structlog
import logging
import sys

def setup_logging(json_output: bool = False):
    """Configure structured logging for the application."""

    shared_processors = [
        structlog.contextvars.merge_contextvars,  # Thread-local context
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
```

**Usage**: `setup_logging(json_output=os.getenv("LOG_FORMAT") == "json")`

**Add to requirements.txt**: `structlog==24.4.0`

**Files**: New `logging_config.py`, modify `server.py`, update all `logger.info(f"...")` → `logger.info("event", key=value)` (can be done incrementally)

---

### O-02 · Application Metrics Collection 🟡Impact 🟡Effort

**What**: Add lightweight metrics counters/gauges exposed via `/api/metrics` endpoint.

**Why**: Currently, the only operational data available is the SDK pool stats and per-project costs. There's no visibility into: request rates, error rates, WebSocket connection count, orchestrator round duration, cache hit rates, DB query latency. Without metrics, problems are invisible until users report them.

**Implementation** — lightweight, no external dependencies:
```python
# metrics.py
import time
from collections import defaultdict
from typing import Any

class Metrics:
    """Lightweight in-process metrics — no Prometheus dependency needed."""

    def __init__(self):
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._start_time = time.time()

    def increment(self, name: str, value: int = 1):
        self._counters[name] += value

    def gauge(self, name: str, value: float):
        self._gauges[name] = value

    def observe(self, name: str, value: float):
        """Record a value for histogram/summary (keeps last 1000)."""
        hist = self._histograms[name]
        hist.append(value)
        if len(hist) > 1000:
            self._histograms[name] = hist[-500:]  # Trim to 500

    def snapshot(self) -> dict[str, Any]:
        return {
            "uptime_seconds": time.time() - self._start_time,
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {
                name: {
                    "count": len(values),
                    "avg": sum(values) / len(values) if values else 0,
                    "p50": sorted(values)[len(values) // 2] if values else 0,
                    "p99": sorted(values)[int(len(values) * 0.99)] if values else 0,
                }
                for name, values in self._histograms.items()
            },
        }

metrics = Metrics()
```

**Instrument key paths**:
```python
# In api.py middleware:
metrics.increment("http_requests_total")
metrics.observe("http_request_duration_seconds", elapsed)

# In sdk_client.py:
metrics.increment("sdk_queries_total")
metrics.observe("sdk_query_duration_seconds", elapsed)
metrics.increment("sdk_errors_total")

# In events.py:
metrics.gauge("websocket_connections", event_bus.subscriber_count)

# In orchestrator.py:
metrics.increment("orchestrator_rounds_total")
metrics.observe("orchestrator_round_duration_seconds", elapsed)
```

**Endpoint**:
```python
@app.get("/api/metrics")
async def get_metrics():
    return metrics.snapshot()
```

**Files**: New `metrics.py`, instrument `dashboard/api.py`, `sdk_client.py`, `orchestrator.py`, `dashboard/events.py`

---

### O-03 · Correlation ID Propagation 🟡Impact 🟢Effort

**What**: Extend the existing `request_id` middleware to propagate correlation IDs through WebSocket events, SDK calls, and orchestrator rounds.

**Why**: Currently, `request_id` is set on HTTP requests but not propagated to the orchestrator or SDK client. When debugging a failed agent call, there's no way to trace it back to the original user message. A correlation ID links: `user sends message → orchestrator receives → delegates to developer → SDK query → CLI subprocess → result → WebSocket event`.

**Implementation**: Use `structlog.contextvars` (or `contextvars` directly) to set the correlation ID:
```python
import contextvars

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar('correlation_id', default='')

# In send_message endpoint:
cid = uuid.uuid4().hex[:12]
correlation_id.set(cid)
logger.info("message_received", correlation_id=cid, project_id=project_id)

# In orchestrator — pass through:
async def start_session(self, task: str, correlation_id: str = ""):
    self._correlation_id = correlation_id or uuid.uuid4().hex[:12]

# In SDK client — include in request_id:
request_id = f"req_{self._correlation_id}_{int(time.monotonic() * 1000) % 10000}"
```

**Files**: `dashboard/api.py`, `orchestrator.py`, `sdk_client.py`

---

### O-04 · Log Rotation with RotatingFileHandler 🟢Impact 🟢Effort

**What**: Add file-based log rotation alongside stdout logging.

**Why**: Running `python server.py` in a terminal or via `nohup` generates unbounded log output. Over days of operation with verbose agent logging, this can fill disk. A 10MB × 5-backup rotation caps log storage at 50MB.

**Implementation** (in `server.py` or `logging_config.py`):
```python
from logging.handlers import RotatingFileHandler

# Add rotating file handler
file_handler = RotatingFileHandler(
    "bot.log",
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
    encoding="utf-8",
)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(file_handler)
```

**Files**: `server.py` (or new `logging_config.py`)

---

### O-05 · Frontend Error Boundary with Reporting 🟡Impact 🟢Effort

**What**: Add React Error Boundaries around major components to catch rendering errors gracefully.

**Why**: A JavaScript error in one component (e.g., undefined property in an agent state update) crashes the entire app. Error Boundaries catch these and show a recovery UI instead of a blank white screen.

**Implementation**:
```tsx
class ErrorBoundary extends React.Component<{children: React.ReactNode, fallback?: React.ReactNode}> {
  state = { hasError: false, error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('Component error:', error, info);
    // Could POST to /api/frontend-error for server-side tracking
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback || (
        <div className="p-4 bg-red-50 border border-red-200 rounded">
          <h3>Something went wrong</h3>
          <p>{this.state.error?.message}</p>
          <button onClick={() => this.setState({ hasError: false })}>
            Try Again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
```

**Files**: New frontend `src/components/ErrorBoundary.tsx`, wrap in `App.tsx`

---

## 4. Developer Experience

### Current State Assessment

| Area | Current Pattern | Best Practice | Gap |
|------|----------------|---------------|-----|
| Type checking | Type hints present, no mypy | mypy strict mode in CI | Hints are decorative |
| API documentation | FastAPI auto-docs (`/api/docs`) | Pydantic response models + examples | Request models only |
| Dev server | `python server.py` + `npm run dev` | Single `make dev` command | Two terminal setup |
| Testing | 131 unit tests, no API/WebSocket tests | Full coverage including integration | Major gap |
| Code formatting | No enforced style | ruff/black + pre-commit | Inconsistent formatting |
| Hot reload | uvicorn reload (backend), Vite HMR (frontend) | Both work, but no proxy setup | CORS workaround needed |

### D-01 · Makefile for Common Operations 🟢Impact 🟢Effort

**What**: Add a `Makefile` with all common development commands.

**Implementation**:
```makefile
.PHONY: dev test lint format build docker-up docker-down

# Start both backend and frontend in dev mode
dev:
	@echo "Starting backend + frontend..."
	@trap 'kill 0' EXIT; \
		python server.py & \
		cd frontend && npm run dev & \
		wait

# Run all tests
test:
	python -m pytest tests/ -v --tb=short

# Run tests with coverage
test-cov:
	python -m pytest tests/ -v --cov=. --cov-report=term-missing --cov-exclude=tests/

# Type checking
typecheck:
	mypy --ignore-missing-imports --no-strict *.py dashboard/

# Lint and format
lint:
	ruff check .

format:
	ruff format .

# Build frontend
build-frontend:
	cd frontend && npm ci && npm run build

# Docker
docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

# Clean
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf frontend/dist .pytest_cache .mypy_cache
```

**Files**: New `Makefile`

---

### D-02 · Pydantic Response Models for All Endpoints 🟡Impact 🟡Effort

**What**: Define Pydantic models for API responses, not just requests.

**Why**: Currently, endpoints return raw dicts. This means: (1) no auto-generated response schemas in `/api/docs`, (2) no runtime validation that responses match the contract, (3) frontend developers have to guess the response shape by reading Python code.

**Implementation example**:
```python
class ProjectResponse(BaseModel):
    project_id: str
    project_name: str
    project_dir: str
    status: str  # "idle" | "running" | "paused"
    is_running: bool
    is_paused: bool
    turn_count: int
    total_cost_usd: float
    agents: list[str]
    multi_agent: bool
    last_message: dict | None = None
    agent_states: dict[str, dict] = {}
    description: str = ""
    message_count: int = 0

class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]

@app.get("/api/projects", response_model=ProjectListResponse)
async def list_projects():
    ...
```

**Files**: New `dashboard/schemas.py`, update `dashboard/api.py` endpoint signatures

---

### D-03 · API Endpoint Tests with TestClient 🟢Impact 🟡Effort

**What**: Add pytest tests for all REST endpoints using FastAPI's `TestClient`.

**Why**: The biggest test coverage gap is the API layer — 25+ endpoints with zero tests. Any refactoring (like the router split in MAINT-01) risks breaking endpoints silently. TestClient tests are fast (no server startup) and catch regressions immediately.

**Implementation** (`tests/test_api.py`):
```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

@pytest.fixture
def client():
    """Create test client with mocked state."""
    with patch('state.session_mgr') as mock_mgr, \
         patch('state.sdk_client') as mock_sdk:
        mock_mgr.list_projects = AsyncMock(return_value=[])
        mock_mgr.is_healthy = AsyncMock(return_value=True)

        from dashboard.api import create_app
        app = create_app()
        yield TestClient(app)

def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("ok", "degraded")
    assert "db" in data
    assert "cli" in data

def test_list_projects_empty(client):
    response = client.get("/api/projects")
    assert response.status_code == 200
    assert response.json()["projects"] == []

def test_create_project_invalid_name(client):
    response = client.post("/api/projects", json={
        "name": "../../../etc/passwd",
        "directory": "/tmp/test",
    })
    assert response.status_code == 400
```

**Files**: New `tests/test_api.py`

---

### D-04 · Pre-commit Hooks with ruff 🟢Impact 🟢Effort

**What**: Add `ruff` for linting/formatting with a pre-commit hook.

**Why**: The codebase has inconsistent formatting (some files use trailing commas, some don't; some use `logger.info(f"...")`, some use `logger.info("...", ...)`). A formatter catches these automatically. `ruff` is 10-100x faster than `flake8` + `black` combined.

**Implementation**:
```toml
# pyproject.toml
[tool.ruff]
target-version = "py311"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B"]
ignore = ["E501"]  # Line length handled by formatter

[tool.ruff.format]
quote-style = "double"
```

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

**Files**: New `pyproject.toml`, new `.pre-commit-config.yaml`

---

### D-05 · Extract Prompts to Markdown Files 🟢Impact 🟢Effort

**What**: Move the 400+ lines of system prompts from `config.py` to separate `.md` files.

**Why**: Currently, editing prompts requires navigating Python string concatenation with escape rules. Prompts can't be diffed cleanly in git, can't be loaded by non-Python tools, and clutter `config.py` (which should be about configuration, not content).

**Implementation**:
```
prompts/
  orchestrator.md    # ORCHESTRATOR_SYSTEM_PROMPT
  solo_agent.md      # SOLO_AGENT_PROMPT
  developer.md       # SUB_AGENT_PROMPTS["developer"]
  reviewer.md        # SUB_AGENT_PROMPTS["reviewer"]
  tester.md          # etc.
  devops.md
  researcher.md
  _collaboration_footer.md  # Shared footer
```

```python
# config.py — replace 400 lines with:
_PROMPTS_DIR = _PROJECT_ROOT / "prompts"

def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning("Prompt file not found: %s", path)
    return ""

ORCHESTRATOR_SYSTEM_PROMPT = _load_prompt("orchestrator")
SOLO_AGENT_PROMPT = _load_prompt("solo_agent")
_COLLABORATION_FOOTER = _load_prompt("_collaboration_footer")
SUB_AGENT_PROMPTS = {
    role: _load_prompt(role) + _COLLABORATION_FOOTER
    for role in ("developer", "reviewer", "tester", "devops", "researcher")
}
```

**Files**: New `prompts/` directory with 7 files, simplify `config.py`

---

### D-06 · pyproject.toml Consolidation 🟢Impact 🟢Effort

**What**: Add a `pyproject.toml` to consolidate tool configuration (pytest, mypy, ruff) and project metadata.

**Implementation**:
```toml
[project]
name = "web-claude-bot"
version = "1.0.0"
requires-python = ">=3.11"
description = "Multi-agent orchestration dashboard for Claude AI"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
warn_return_any = true
warn_unused_configs = true

[tool.ruff]
target-version = "py311"
line-length = 120
```

**Files**: New `pyproject.toml`

---

### D-07 · TypeScript Strict Mode for Frontend 🟡Impact 🟡Effort

**What**: Enable stricter TypeScript checking to catch bugs at compile time.

**Current** (`frontend/tsconfig.json`): likely has `strict: false` or minimal strictness settings.

**Target additions**:
```json
{
  "compilerOptions": {
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noImplicitReturns": true,
    "forceConsistentCasingInFileNames": true
  }
}
```

**Files**: `frontend/tsconfig.json`, fix resulting type errors in frontend source

---

## 5. Deployment & Operations

### Current State Assessment

| Area | Current Pattern | Best Practice | Gap |
|------|----------------|---------------|-----|
| Docker build | Multi-stage (3 stages), works | Layer caching could be better | CLI install duplicated |
| Docker image size | python:3.11-slim + Node.js + npm | Minimize Node.js footprint | Full npm installed at runtime |
| Environment management | `.env` file + `python-dotenv` | Works, but no validation | No `.env` schema |
| Backup | None | SQLite backup to file | Data loss risk |
| Update strategy | Manual `git pull` + restart | Versioned releases or auto-update | No version tracking |

### DEP-01 · Docker Image Size Reduction 🟡Impact 🟡Effort

**What**: Reduce Docker image size by removing build-time dependencies from runtime.

**Current image layers include**:
- `python:3.11-slim` (~120MB)
- `nodejs + npm` via apt (~150MB) — only needed for Claude CLI
- Python packages (~50MB)
- App code + frontend dist (~5MB)

**Optimizations**:
1. Copy Claude CLI binary from cli-builder stage instead of installing npm at runtime
2. Remove npm from runtime stage
3. Use `--no-install-recommends` consistently

```dockerfile
# Stage 2: Extract Claude CLI binary
FROM node:20-alpine AS cli-builder
RUN npm install -g @anthropic-ai/claude-code
# Find the actual binary location
RUN which claude && ls -la $(which claude)

# Stage 3: Python runtime — NO npm needed
FROM python:3.11-slim AS runtime
# Only copy the Claude CLI binary and its node_modules
COPY --from=cli-builder /usr/local/bin/claude /usr/local/bin/claude
COPY --from=cli-builder /usr/local/lib/node_modules/@anthropic-ai /usr/local/lib/node_modules/@anthropic-ai
# Node.js runtime still needed (Claude CLI is a Node app), but npm is not
RUN apt-get update && apt-get install -y --no-install-recommends nodejs curl \
    && rm -rf /var/lib/apt/lists/*
```

**Expected savings**: ~100MB (removing npm + npm cache)

**Files**: `Dockerfile`

---

### DEP-02 · SQLite Database Backup 🟢Impact 🟢Effort

**What**: Add periodic SQLite backup using the `.backup` API.

**Why**: The SQLite database in `data/sessions.db` contains all project history, messages, and task history. A corruption event (disk failure, OOM during write) could lose all data. SQLite's online backup API creates a consistent snapshot without stopping the server.

**Implementation** (`session_manager.py`):
```python
async def backup(self, backup_path: str | None = None):
    """Create a backup of the database using SQLite's online backup API."""
    if not self._db:
        return

    backup_path = backup_path or f"{self.db_path}.backup"

    # Use SQLite's built-in backup
    async with aiosqlite.connect(backup_path) as backup_db:
        await self._db.backup(backup_db)

    logger.info("Database backed up to %s", backup_path)
```

**Add to cleanup loop** (`server.py`):
```python
async def _cleanup_loop():
    while True:
        await asyncio.sleep(3600)  # Every hour
        if state.session_mgr:
            await state.session_mgr.cleanup_expired()
            await state.session_mgr.backup()  # Hourly backup
```

**Files**: `session_manager.py`, `server.py`

---

### DEP-03 · Environment Variable Validation at Startup 🟢Impact 🟢Effort

**What**: Validate all environment variables at startup with clear error messages.

**Why**: `validate_config()` checks computed values but not the raw environment. A typo in `.env` (e.g., `DASHBOARD_PROT=8080` instead of `DASHBOARD_PORT=8080`) silently falls through to defaults. Explicit validation catches this at startup.

**Implementation** — add to `config.py`:
```python
def validate_environment() -> list[str]:
    """Validate environment variables and return warnings for unknown ones."""
    KNOWN_ENV_VARS = {
        "DASHBOARD_PORT", "DASHBOARD_API_KEY", "CORS_ORIGINS",
        "CLAUDE_CLI_PATH", "CLAUDE_PROJECTS_DIR",
        "MAX_TURNS_PER_CYCLE", "MAX_BUDGET_USD", "AGENT_TIMEOUT_SECONDS",
        "SESSION_TIMEOUT_SECONDS", "SDK_MAX_TURNS_PER_QUERY",
        "SDK_MAX_BUDGET_PER_QUERY", "SESSION_EXPIRY_HOURS",
        "MAX_ORCHESTRATOR_LOOPS", "RATE_LIMIT_SECONDS",
        "BUDGET_WARNING_THRESHOLD", "STALL_ALERT_SECONDS",
        "PIPELINE_MAX_STEPS", "SCHEDULER_CHECK_INTERVAL",
        "CONVERSATION_STORE_DIR", "MAX_USER_MESSAGE_LENGTH",
        "PREDEFINED_PROJECTS", "LOG_FORMAT",
    }

    warnings = []
    for key in os.environ:
        # Check for close misspellings of known vars
        if key.startswith(("DASHBOARD_", "CLAUDE_", "SDK_", "MAX_", "SESSION_")):
            if key not in KNOWN_ENV_VARS:
                warnings.append(f"Unknown env var '{key}' — did you mean one of: {KNOWN_ENV_VARS}?")
    return warnings
```

**Files**: `config.py`, call from `server.py`

---

### DEP-04 · Version Tracking 🟢Impact 🟢Effort

**What**: Add a `__version__` constant and expose it in health check and startup log.

**Implementation**:
```python
# __version__.py
__version__ = "1.0.0"
__build_date__ = "2026-03-08"
```

```python
# In health check:
from __version__ import __version__, __build_date__

@app.get("/api/health")
async def health_check():
    return {
        "status": overall,
        "version": __version__,
        "build_date": __build_date__,
        ...
    }
```

**Files**: New `__version__.py`, update `dashboard/api.py`

---

### DEP-05 · Docker Compose Profiles for Dev vs Prod 🟡Impact 🟢Effort

**What**: Add profiles to `docker-compose.yml` for development (with volume mounts, debug logs) vs production (optimized, no source mount).

**Implementation**:
```yaml
services:
  app:
    build: .
    ports:
      - "8080:8080"
    environment:
      - LOG_FORMAT=json
    profiles: ["prod"]

  app-dev:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - .:/app
      - ./data:/app/data
    environment:
      - LOG_FORMAT=text
      - PYTHONDONTWRITEBYTECODE=0
    command: ["python", "-m", "uvicorn", "dashboard.api:create_app", "--reload", "--host", "0.0.0.0", "--port", "8080"]
    profiles: ["dev"]
```

**Usage**: `docker compose --profile dev up` or `docker compose --profile prod up`

**Files**: `docker-compose.yml`

---

## Priority Matrix — Full Roadmap

### 🔥 Tier 1: Quick Wins (High Impact, Low Effort) — Do This Week

| ID | Item | Impact | Effort | Category |
|----|------|--------|--------|----------|
| P-01 | SQLite PRAGMA tuning | 🟢 | 🟢 | Performance |
| P-03 | Wrap blocking I/O with `asyncio.to_thread()` | 🟢 | 🟢 | Performance |
| R-02 | WebSocket ping/pong heartbeat | 🟢 | 🟢 | Reliability |
| R-07 | Request timeout middleware | 🟢 | 🟢 | Reliability |
| O-04 | Log rotation | 🟢 | 🟢 | Observability |
| D-01 | Makefile for common operations | 🟢 | 🟢 | DX |
| D-04 | Pre-commit hooks with ruff | 🟢 | 🟢 | DX |
| D-05 | Extract prompts to markdown files | 🟢 | 🟢 | DX |
| D-06 | pyproject.toml consolidation | 🟢 | 🟢 | DX |
| DEP-02 | SQLite database backup | 🟢 | 🟢 | Deployment |
| DEP-03 | Environment variable validation | 🟢 | 🟢 | Deployment |
| DEP-04 | Version tracking | 🟢 | 🟢 | Deployment |

**Estimated total effort**: 4-6 hours

---

### ⚡ Tier 2: High-Value Improvements (High/Medium Impact, Medium Effort) — Next Sprint

| ID | Item | Impact | Effort | Category |
|----|------|--------|--------|----------|
| P-02 | In-memory TTL cache for hot data | 🟢 | 🟡 | Performance |
| P-04 | Git operation caching | 🟡 | 🟡 | Performance |
| P-05 | Frontend code splitting | 🟡 | 🟡 | Performance |
| P-07 | Optimistic frontend updates | 🟡 | 🟡 | Performance |
| R-01 | Circuit breaker for Claude CLI | 🟢 | 🟡 | Reliability |
| R-03 | Readiness/liveness health checks | 🟡 | 🟢 | Reliability |
| R-04 | Subprocess lifecycle management | 🟡 | 🟡 | Reliability |
| R-05 | Graceful degradation | 🟡 | 🟡 | Reliability |
| O-01 | Structured logging with structlog | 🟢 | 🟡 | Observability |
| O-02 | Application metrics collection | 🟡 | 🟡 | Observability |
| O-03 | Correlation ID propagation | 🟡 | 🟢 | Observability |
| O-05 | Frontend error boundaries | 🟡 | 🟢 | Observability |
| D-02 | Pydantic response models | 🟡 | 🟡 | DX |
| D-03 | API endpoint tests | 🟢 | 🟡 | DX |
| DEP-01 | Docker image size reduction | 🟡 | 🟡 | Deployment |
| DEP-05 | Docker Compose profiles | 🟡 | 🟢 | Deployment |

**Estimated total effort**: 20-30 hours

---

### 🏗️ Tier 3: Architectural Improvements (Medium Impact, High Effort) — Backlog

| ID | Item | Impact | Effort | Category |
|----|------|--------|--------|----------|
| P-06 | Read/write DB connection separation | 🟡 | 🔴 | Performance |
| P-08 | Cursor-based pagination | 🔴 | 🟡 | Performance |
| R-06 | Automatic session recovery on crash | 🟡 | 🔴 | Reliability |
| D-07 | TypeScript strict mode | 🟡 | 🟡 | DX |

**Estimated total effort**: 15-20 hours

---

## Recommended Implementation Order

### Week 1: Foundation (Tier 1)
1. **P-01**: SQLite PRAGMAs (10 min)
2. **DEP-04**: Version tracking (15 min)
3. **D-06**: pyproject.toml (15 min)
4. **O-04**: Log rotation (20 min)
5. **R-07**: Request timeout middleware (15 min)
6. **R-02**: WebSocket heartbeat (30 min)
7. **P-03**: asyncio.to_thread for blocking I/O (45 min)
8. **D-05**: Extract prompts to markdown (1 hour)
9. **D-01**: Makefile (30 min)
10. **DEP-02**: SQLite backup (30 min)
11. **DEP-03**: Environment validation (30 min)
12. **D-04**: Pre-commit hooks (30 min)

### Week 2: Resilience (Tier 2, Reliability + Observability)
1. **R-01**: Circuit breaker (1.5 hours)
2. **O-01**: Structured logging with structlog (2 hours)
3. **O-02**: Application metrics (2 hours)
4. **O-03**: Correlation IDs (45 min)
5. **R-03**: Health check separation (30 min)
6. **R-04**: Subprocess lifecycle (1.5 hours)
7. **R-05**: Graceful degradation (2 hours)

### Week 3: Performance + DX (Tier 2, Performance + DX)
1. **P-02**: TTL cache (1.5 hours)
2. **P-04**: Git caching (1 hour)
3. **D-03**: API endpoint tests (3 hours)
4. **D-02**: Pydantic response models (2 hours)
5. **P-05**: Frontend code splitting (1 hour)
6. **P-07**: Optimistic updates (1.5 hours)
7. **O-05**: Error boundaries (30 min)

### Week 4: Polish (Tier 2 remainder + Tier 3 priorities)
1. **DEP-01**: Docker image optimization (1.5 hours)
2. **DEP-05**: Docker Compose profiles (30 min)
3. **D-07**: TypeScript strict mode (2 hours)
4. **P-06**: Read/write connection separation (3 hours)

---

## Cross-Reference with Existing Plans

This roadmap **complements** (does not replace) the existing `.nexus/IMPROVEMENT_PLAN.md`:

| Existing Plan | This Roadmap |
|---------------|-------------|
| SEC-01..06: Security fixes | Not duplicated — do those first |
| REL-01..07: Bug-level reliability | R-01..07 here are architectural improvements |
| PERF-01..06: Code-level perf fixes | P-01..08 here are system-level optimizations |
| CQ-01..06: Code quality | D-01..07 here are DX improvements |
| MAINT-01..08: Maintainability | Covered by refactoring items in existing plan |

**Do the existing IMPROVEMENT_PLAN.md items first** (especially P0 security), then layer on this roadmap.

---

## Sources & References

1. SQLite WAL + PRAGMA optimization — [SQLite official docs](https://sqlite.org/pragma.html), [Ben Johnson's Litestream recommendations](https://litestream.io/tips/)
2. FastAPI WebSocket best practices — [FastAPI docs](https://fastapi.tiangolo.com/advanced/websockets/), [Starlette WebSocket handling](https://www.starlette.io/websockets/)
3. Circuit breaker pattern — [Microsoft Cloud Design Patterns](https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker), [Martin Fowler](https://martinfowler.com/bliki/CircuitBreaker.html)
4. structlog — [structlog documentation](https://www.structlog.org/en/stable/)
5. React.lazy code splitting — [React docs](https://react.dev/reference/react/lazy)
6. Vite build optimization — [Vite docs](https://vitejs.dev/guide/build.html)
7. aiosqlite best practices — [aiosqlite GitHub](https://github.com/omnilib/aiosqlite)
8. Health check patterns (liveness/readiness) — [Kubernetes docs](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
9. ruff linter — [Astral ruff docs](https://docs.astral.sh/ruff/)
10. Python subprocess management — [Python asyncio subprocess docs](https://docs.python.org/3/library/asyncio-subprocess.html)
