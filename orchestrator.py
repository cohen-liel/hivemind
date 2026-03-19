from __future__ import annotations

import asyncio
import collections
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from config import (
    AGENT_RETRY_DELAY,
    ASYNC_WAIT_TIMEOUT,
    BUDGET_WARNING_THRESHOLD,
    CONVERSATION_LOG_MAXLEN,
    DAG_MAX_CONCURRENT_GRAPHS,
    GIT_DIFF_TIMEOUT,
    MAX_ANYIO_RETRIES,
    MAX_BUDGET_USD,
    MAX_ORCHESTRATOR_LOOPS,
    MAX_TURNS_PER_CYCLE,
    RATE_LIMIT_SECONDS,
    SESSION_TIMEOUT_SECONDS,
    SPECIALIST_PROMPTS,
    USE_DAG_EXECUTOR,
)

# --- Typed Contract Protocol (new DAG-based system) ---
# Imported lazily inside _run_dag_session to avoid circular imports
# (orchestrator → pm_agent → state → orchestrator)
from contracts import TaskInput, TaskOutput
from sdk_client import ClaudeSDKManager, SDKResponse
from src.storage.platform_session import PlatformSessionManager as SessionManager

logger = logging.getLogger(__name__)

# AGENT_EMOJI — derived from AGENT_REGISTRY in config.py (single source of truth)
import orch_agents

# ── Extracted modules (reduce orchestrator.py size) ──────────────────
import orch_experience
import orch_review
import orch_watchdog
from architect_agent import ArchitectureBrief, run_architect_review, should_run_architect
from config import AGENT_EMOJI
from cross_project_memory import CrossProjectMemory

# Regex to parse <delegate> blocks from orchestrator output
# Match everything between <delegate> and </delegate> tags, then parse JSON separately
_DELEGATE_RE = re.compile(
    r"<delegate>\s*(.*?)\s*</delegate>",
    re.DOTALL,
)


@dataclass
class Message:
    agent_name: str
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    cost_usd: float = 0.0


@dataclass
class Delegation:
    agent: str
    task: str
    context: str = ""
    skills: list[str] = field(default_factory=list)


class OrchestratorManager:
    """Orchestrator-based agent management, replacing the round-robin AgentManager.

    The orchestrator agent receives user tasks and decides whether to handle
    directly or delegate to sub-agents via <delegate> blocks.
    """

    def _drain_cancellations(self) -> int:
        """Drain pending cancellation requests from the current asyncio task.

        The anyio cancel-scope bug can accumulate multiple CancelledError
        requests on a single task (cancelling_count grows by 2+ per spurious
        event). A single ct.uncancel() only decrements by 1, leaving the
        task in a cancelled state that immediately re-raises on the next await.

        FIX(C-5): Only drains when ``_stop_event`` is NOT set.  When
        ``_stop_event`` IS set the CancelledError is *legitimate* (user
        pressed Stop or system shutdown) and must NOT be suppressed — the
        caller should let it propagate.

        Returns:
            Number of cancellations drained (0 if stop_event is set).
        """
        # Legitimate cancellation — do NOT drain
        if self._stop_event.is_set():
            return 0
        ct = asyncio.current_task()
        if ct is None or not hasattr(ct, "uncancel"):
            return 0
        drained = 0
        while hasattr(ct, "cancelling") and ct.cancelling() > 0:
            ct.uncancel()
            drained += 1
            if drained > 100:  # Safety — should never happen
                break
        return drained

    # Agents that modify files — must run sequentially to avoid conflicts
    _WRITER_ROLES = {
        "developer",
        "devops",
        "backend_developer",
        "frontend_developer",
        "database_expert",
        "typescript_architect",
        "python_backend",
    }

    # Read-only / analysis agents — safe to run in parallel
    # (Kept consistent with dag_executor._READER_ROLES)
    _READER_ROLES = {
        "researcher",
        "reviewer",
        "security_auditor",
        "ux_critic",
        "test_engineer",
        "tester",
        "memory",
    }

    def __init__(
        self,
        project_name: str,
        project_dir: str,
        sdk: ClaudeSDKManager,
        session_mgr: SessionManager,
        user_id: int,
        project_id: str,
        on_update: Callable[[str], Awaitable[None]] | None = None,
        on_result: Callable[[str], Awaitable[None]] | None = None,
        on_final: Callable[[str], Awaitable[None]] | None = None,
        on_event: Callable[[dict], Awaitable[None]] | None = None,
        multi_agent: bool = True,
        mode: str = "autonomous",
    ):
        self.project_name = project_name
        # Resolve to absolute path and validate it's inside CLAUDE_PROJECTS_ROOT
        from config import CLAUDE_PROJECTS_ROOT, SANDBOX_ENABLED

        project_dir_resolved = str(Path(project_dir).resolve())
        if SANDBOX_ENABLED:
            root_resolved = str(Path(CLAUDE_PROJECTS_ROOT).resolve())
            if (
                not project_dir_resolved.startswith(root_resolved + "/")
                and project_dir_resolved != root_resolved
            ):
                raise ValueError(
                    f"Project directory {project_dir!r} is outside allowed root {CLAUDE_PROJECTS_ROOT!r}"
                )
        self.project_dir = project_dir_resolved
        self.sdk = sdk
        self.session_mgr = session_mgr
        self.user_id = user_id
        self.project_id = project_id
        self.on_update = on_update
        self.on_result = on_result
        self.on_final = on_final
        self.on_event = on_event
        self.multi_agent = multi_agent
        # Execution mode: "autonomous" (execute immediately) or "interactive" (ask first)
        from config import AGENT_MODE_DEFAULT

        self.mode: str = mode if mode in ("autonomous", "interactive") else AGENT_MODE_DEFAULT

        self.conversation_log: collections.deque[Message] = collections.deque(
            maxlen=CONVERSATION_LOG_MAXLEN
        )
        self._agents_used: set[str] = (
            set()
        )  # All agent roles that have run — persists even if log is trimmed
        self.is_running = False
        self.is_paused = False
        self.total_cost_usd = 0.0
        self.turn_count = 0

        # Live state tracking for dashboard
        self.current_agent: str | None = None
        self.current_tool: str | None = None
        self.agent_states: dict[str, dict] = {}  # agent_name -> {state, task, cost, turns, ...}

        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

        # Message queue — replaces single-slot _user_injection to prevent lost messages
        # when multiple agents or the user send messages concurrently.
        self._message_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        # HITL approval mechanism
        self._approval_event = asyncio.Event()
        self._approval_result: bool = True  # True = approved, False = rejected
        self._pending_approval: str | None = None

        # Shared context accumulator — passes summary of previous rounds to sub-agents
        self.shared_context: list[str] = []

        # Track completed rounds for summaries and final reporting
        self._completed_rounds: list[str] = []

        # Track fire-and-forget tasks to prevent GC and log errors
        self._background_tasks: set[asyncio.Task] = set()

        # Guard flag to prevent duplicate auto-restarts.
        # Without this, multiple rapid _on_task_done callbacks could each
        # drain the queue and schedule concurrent start_session() calls.
        self._restarting: bool = False

        # Current orchestrator loop count (readable by /live endpoint)
        self._current_loop: int = 0
        # Effective budget (respects per-project override)
        self._effective_budget: float = MAX_BUDGET_USD

        # DAG execution state — exposed via /live for cross-tab/refresh recovery
        self._dag_task_statuses: dict[str, str] = {}  # task_id → "working"/"completed"/"failed"
        self._current_dag_graph: dict | None = None  # serialized TaskGraph for the active session

        # Agent silence watchdog — detects agents that stop producing output
        self._watchdog_task: asyncio.Task | None = None
        # Track which agents have already had a silence alert emitted (avoids spamming)
        self._silence_alerted: set[str] = set()

        # Rate limiting: minimum gap between orchestrator API calls
        self._last_orch_call_time: float = 0.0
        # Budget warning sent flag (prevents duplicate warnings)
        self._budget_warning_sent: bool = False
        # Orchestrator error retry counter
        self._orch_error_retries: int = 0
        # Last user message (for state persistence)
        self._last_user_message: str = ""
        # Stuck escalation hint (injected into review prompt when stuck detected)
        self._stuck_escalation_hint: str = ""

        # ── Parallel task ingestion ─────────────────────────────────────────
        # When a new user message arrives while a graph is already executing,
        # it is placed in _graph_ingestion_queue rather than dropped.
        # _graph_semaphore bounds how many full graph executions may run
        # concurrently (across independent calls to start_session).
        # A background _graph_dispatcher drains the queue and starts sessions
        # as semaphore slots become available.
        self._graph_ingestion_queue: asyncio.Queue[str] = asyncio.Queue()
        self._graph_semaphore: asyncio.Semaphore = asyncio.Semaphore(DAG_MAX_CONCURRENT_GRAPHS)
        self._graph_dispatcher: asyncio.Task[None] | None = None
        # Count of graph executions currently running under the semaphore
        self._active_graph_count: int = 0

    # Checkpoint every N orchestrator rounds (periodic safety-net)
    _CHECKPOINT_INTERVAL_ROUNDS = 3

    @property
    def agent_names(self) -> list[str]:
        """Return the list of active agent role names."""
        names = ["orchestrator"]
        if self.multi_agent:
            # All known roles from the centralized AGENT_REGISTRY
            from config import get_all_role_names

            all_roles = get_all_role_names(include_legacy=True)
            names.extend(sorted(all_roles - {"orchestrator"}))
        return names

    @property
    def is_multi_agent(self) -> bool:
        """Return True if the session uses more than one agent."""
        return self.multi_agent

    def _create_background_task(self, coro) -> asyncio.Task:
        """Create a background task with proper lifecycle management.

        - Prevents GC from collecting the task (strong reference in self._background_tasks)
        - Logs errors instead of silently swallowing them
        - Auto-removes from the set when done
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _on_done(t: asyncio.Task):
            self._background_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.error(f"[{self.project_id}] Background task failed: {exc}", exc_info=exc)

        task.add_done_callback(_on_done)
        return task

    # ── Checkpoint & Restore ─────────────────────────────────────────────

    async def _checkpoint_state(self, *, status: str = "running"):
        """Checkpoint full orchestrator state to DB.

        Serializes conversation_log, shared_context, completed_rounds,
        agents_used, and DAG progress into the existing orchestrator_state
        columns (shared_context, agent_states) as enriched JSON blobs.
        """
        # Build enriched context blob — fits in the shared_context TEXT column
        checkpoint_context = {
            "shared_context": self.shared_context[-20:],
            "conversation_log": [
                {
                    "agent_name": m.agent_name,
                    "role": m.role,
                    "content": m.content[:500],
                    "timestamp": m.timestamp,
                    "cost_usd": m.cost_usd,
                }
                for m in list(self.conversation_log)[-50:]  # last 50 messages
            ],
            "completed_rounds": self._completed_rounds[-30:],
            "agents_used": list(self._agents_used),
        }

        # Build enriched agent state blob — fits in the agent_states TEXT column
        checkpoint_agents = {
            "agent_states": {k: dict(v) for k, v in dict(self.agent_states).items()},
            "dag_task_statuses": dict(self._dag_task_statuses),
            "dag_graph": self._current_dag_graph,
        }

        try:
            await self.session_mgr.save_orchestrator_state(
                project_id=self.project_id,
                user_id=self.user_id,
                status=status,
                current_loop=self._current_loop,
                turn_count=self.turn_count,
                total_cost_usd=self.total_cost_usd,
                shared_context=checkpoint_context,
                agent_states=checkpoint_agents,
                last_user_message=self._last_user_message,
            )
            logger.info(
                f"[{self.project_id}] Checkpoint saved "
                f"(loop={self._current_loop}, cost=${self.total_cost_usd:.4f}, "
                f"status={status})"
            )
        except Exception as e:
            logger.warning(f"[{self.project_id}] Checkpoint save failed: {e}")

    def _checkpoint_async(self, *, status: str = "running"):
        """Schedule a non-blocking checkpoint as a background task.

        Returns immediately — the actual DB write happens asynchronously
        so the main orchestration loop is never blocked.
        """
        self._create_background_task(self._checkpoint_state(status=status))

    async def restore_from_checkpoint(self, checkpoint: dict) -> bool:
        """Restore orchestrator state from a checkpoint dict.

        Called during resume to reconstruct the OrchestratorManager to the
        exact state saved at the last checkpoint — including conversation_log,
        shared_context, completed_rounds, agents_used, and DAG progress.

        Returns True on success, False on failure.
        """
        try:
            # Restore scalar state
            self._current_loop = checkpoint.get("current_loop", 0)
            self.turn_count = checkpoint.get("turn_count", 0)
            self.total_cost_usd = checkpoint.get("total_cost_usd", 0.0)
            self._last_user_message = checkpoint.get("last_user_message", "")

            # Restore enriched context (new dict format)
            ctx = checkpoint.get("shared_context", [])
            if isinstance(ctx, dict):
                # New enriched format
                self.shared_context = ctx.get("shared_context", [])

                # Restore conversation log
                for msg_data in ctx.get("conversation_log", []):
                    self.conversation_log.append(
                        Message(
                            agent_name=msg_data.get("agent_name", "unknown"),
                            role=msg_data.get("role", "Unknown"),
                            content=msg_data.get("content", ""),
                            timestamp=msg_data.get("timestamp", 0),
                            cost_usd=msg_data.get("cost_usd", 0),
                        )
                    )
                self._completed_rounds = ctx.get("completed_rounds", [])
                agents_used_raw = ctx.get("agents_used", [])
                if not isinstance(agents_used_raw, list):
                    logger.warning(
                        f"[{self.project_id}] Checkpoint agents_used is not a list "
                        f"(got {type(agents_used_raw).__name__}) — resetting to empty"
                    )
                    agents_used_raw = []
                self._agents_used = set(agents_used_raw)
            elif isinstance(ctx, list):
                # Legacy format: just a list of context strings
                self.shared_context = ctx

            # Restore agent states (new enriched format)
            agents_blob = checkpoint.get("agent_states", {})
            if isinstance(agents_blob, dict):
                if "agent_states" in agents_blob:
                    # New enriched format
                    self.agent_states = agents_blob.get("agent_states", {})
                    self._dag_task_statuses = agents_blob.get("dag_task_statuses", {})
                    self._current_dag_graph = agents_blob.get("dag_graph")
                else:
                    # Legacy format: flat dict of agent states
                    self.agent_states = agents_blob

            logger.info(
                f"[{self.project_id}] State restored from checkpoint — "
                f"loop={self._current_loop}, turns={self.turn_count}, "
                f"cost=${self.total_cost_usd:.4f}, "
                f"agents={list(self.agent_states.keys())}, "
                f"conversation_log={len(self.conversation_log)} msgs, "
                f"completed_rounds={len(self._completed_rounds)}"
            )
            return True
        except Exception as e:
            logger.error(
                f"[{self.project_id}] Checkpoint restore failed: {e}",
                exc_info=True,
            )
            return False

    def _on_task_done(self, task: asyncio.Task):
        """Callback attached to the main _run_orchestrator task.

        Catches silent crashes that would otherwise go unnoticed and
        auto-restarts if there are pending messages in the queue.
        """
        if task.cancelled():
            return
        # If this task was replaced by a retry (spurious CancelledError recovery),
        # skip cleanup — the replacement task will handle it when it finishes.
        if self._task is not None and self._task is not task:
            logger.info(
                f"[{self.project_id}] _on_task_done: skipping — this task was replaced by a retry "
                f"(old={task.get_name()}, current={self._task.get_name()})"
            )
            return
        exc = task.exception()
        if exc:
            logger.error(
                f"[{self.project_id}] Orchestrator task crashed: {exc}",
                exc_info=exc,
            )
        # Drain ONE pending message from the queue and restart with it.
        # Remaining messages stay in the queue — the restarted session's main
        # loop will pick them up one per iteration so each is processed
        # independently (Bug #1 + #2 fix: preserve agent_name, no merge).
        # The _restarting flag prevents duplicate concurrent restarts.
        if self._stop_event.is_set() or self._restarting:
            return
        next_agent: str | None = None
        next_msg: str | None = None
        try:
            next_agent, next_msg = self._message_queue.get_nowait()
            remaining = self._message_queue.qsize()
            logger.info(
                f"[{self.project_id}] _on_task_done: dequeued message for "
                f"'{next_agent}' ({remaining} still queued) — "
                f"lifecycle: enqueued → drained"
            )
        except asyncio.QueueEmpty:
            pass
        except Exception as _drain_exc:
            logger.error(
                f"[{self.project_id}] Error draining message queue: {_drain_exc}",
                exc_info=True,
            )
        if next_msg is not None:
            self._restarting = True
            logger.info(
                f"[{self.project_id}] 1 pending message found after task ended "
                f"(target: '{next_agent}') — auto-restarting"
            )
            self._create_background_task(
                self._restart_with_message(next_msg, target_agent=next_agent)
            )
        else:
            # No in-memory messages — check persistent DB queue
            self._create_background_task(self._drain_db_queue())

    async def _restart_with_message(self, message: str, *, target_agent: str | None = None):
        """Wrapper that resets the _restarting guard after start_session completes.

        Without this wrapper, _restarting would stay True forever if start_session
        raised an exception, preventing all future auto-restarts.

        Args:
            message: The user message text.
            target_agent: The agent the message was originally directed to.
                When provided, the message is prefixed with an ``[User message
                to <agent>]`` header so the orchestrator routes it correctly
                (Bug #3 fix: preserve agent context through restart).
        """
        try:
            if target_agent:
                formatted = f"[User message to {target_agent}]:\n{message}"
                logger.info(
                    f"[{self.project_id}] _restart_with_message: preserving "
                    f"target agent '{target_agent}' — lifecycle: processed"
                )
            else:
                formatted = message
                logger.info(
                    f"[{self.project_id}] _restart_with_message: no target "
                    f"agent (orchestrator default) — lifecycle: processed"
                )
            await self.start_session(formatted)
        finally:
            self._restarting = False

    async def _drain_db_queue(self):
        """Check the persistent DB queue and auto-start next message if one exists."""
        if self._stop_event.is_set() or self._restarting:
            return
        import state as _state

        smgr = getattr(_state, "session_mgr", None)
        if not smgr:
            return
        try:
            next_msg = await smgr.dequeue_next_message(self.project_id)
        except Exception as e:
            logger.warning(f"[{self.project_id}] DB queue drain error: {e}")
            return
        if next_msg:
            self._restarting = True
            logger.info(f"[{self.project_id}] DB queue: auto-starting next queued message")
            self._create_background_task(self._restart_with_message(next_msg))

    async def _notify(self, text: str):
        """Send a progress/status update to the client."""
        if self.on_update:
            try:
                await self.on_update(text)
            except Exception as e:
                logger.error(f"Update callback error: {e}", exc_info=True)

    async def _send_result(self, text: str):
        """Send a final result message to the client."""
        if self.on_result:
            try:
                await self.on_result(text)
            except Exception as e:
                logger.error(f"Result callback error: {e}", exc_info=True)
        elif self.on_update:
            # Fallback to on_update if on_result not set
            try:
                await self.on_update(text)
            except Exception as e:
                logger.error(f"Update callback error: {e}", exc_info=True)

    async def _send_final(self, text: str):
        """Send the final clean message (deletes all intermediates, stays forever).

        Also persists to SQLite so the message survives a browser refresh —
        without this, a page reload during/after task completion shows a blank result
        because the event_bus has no subscribers to catch the WS-only event.
        """
        # Persist to DB first so it survives regardless of WS connectivity
        if self.session_mgr and self.project_id:
            self._create_background_task(
                self.session_mgr.add_message(
                    self.project_id,
                    "system",
                    "System",
                    text,
                    0.0,
                )
            )

        if self.on_final:
            try:
                await self.on_final(text)
            except Exception as e:
                logger.error(f"Final callback error: {e}", exc_info=True)
        else:
            # Fallback to on_result
            await self._send_result(text)

    async def _emit_event(self, event_type: str, **data):
        """Emit a structured event for the dashboard."""
        if self.on_event:
            try:
                event = {"type": event_type, "timestamp": time.time(), **data}
                await self.on_event(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}", exc_info=True)

    # ── Stuckness event broadcasting ──

    async def _emit_stuckness_event(self, stuck_info: dict):
        """Broadcast a stuckness_detected event for the dashboard.

        Maps internal stuck_info dict (signal, severity, strategy, details)
        to the public event schema with category, severity, description,
        affected_agent, and suggested_action.
        """
        # Map internal signal names to public category names
        category = stuck_info["signal"]  # text_similarity, repeated_errors, etc.
        severity = stuck_info["severity"]  # 'warning' | 'critical'
        description = stuck_info["details"]
        suggested_action = stuck_info["strategy"]

        # Determine affected agent — most categories are orchestrator-level,
        # but we check agent_states for the currently active agent
        affected_agent: str | None = None
        for name, state in list(
            self.agent_states.items()
        ):  # snapshot prevents RuntimeError if dict is modified concurrently
            if state.get("state") == "working" and name != "orchestrator":
                affected_agent = name
                break

        await self._emit_event(
            "stuckness_detected",
            category=category,
            severity=severity,
            description=description,
            affected_agent=affected_agent,
            suggested_action=suggested_action,
        )

    # ── Pre-task question detection ────────────────────────────────────────

    _QUESTION_PHRASES = (
        "should i",
        "do you want",
        "which approach",
        "would you like",
        "can you clarify",
        "could you clarify",
        "please clarify",
        "what do you mean",
        "which one",
        "do you prefer",
    )

    def _has_question(self, text: str) -> bool:
        """Return True if *text* contains a question that needs user input.

        Triggers when the text ends with '?' or contains known question phrases.
        Used to pause execution and surface the question to the user before
        dispatching agent tasks.
        """
        stripped = text.strip()
        if stripped.endswith("?"):
            return True
        lower = stripped.lower()
        return any(phrase in lower for phrase in self._QUESTION_PHRASES)

    async def _pause_for_question(self, question_text: str) -> str | None:
        """Pause execution, surface a question to the user, and wait for their reply.

        Emits a ``pre_task_question`` event so the frontend can display the question
        inline (not as a blocking modal).  Waits for the next user message via the
        ``_message_queue`` (same mechanism as inject_user_message).

        Returns the user's reply text, or None if the session was stopped while
        waiting.
        """
        logger.info(
            "[%s] Orchestrator has a question — pausing before task dispatch",
            self.project_id,
        )
        await self._emit_event(
            "pre_task_question",
            question=question_text,
            project_id=self.project_id,
        )
        await self._send_result(
            f"\u2754 **Question before I start:**\n\n{question_text}\n\n"
            "_Reply to continue, or say 'go ahead' to proceed with my best guess._"
        )

        # Pause the session so the watchdog doesn't fire during the wait
        await self._self_pause(reason="waiting for user answer to pre-task question")

        # Wait for the user's reply (or a stop signal)
        while True:
            if self._stop_event.is_set():
                return None
            try:
                _agent_name, reply = self._message_queue.get_nowait()
                logger.info(
                    "[%s] Received user reply to pre-task question (%d chars)",
                    self.project_id,
                    len(reply),
                )
                # Resume execution
                self.resume()
                return reply
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.5)

    # ── Agent silence watchdog ──

    def _start_silence_watchdog(self):
        """Start the agent silence watchdog task. Delegated to orch_watchdog."""
        orch_watchdog.start_silence_watchdog(self)

    async def _stop_silence_watchdog(self):
        """Cancel the agent silence watchdog task. Delegated to orch_watchdog."""
        await orch_watchdog.stop_silence_watchdog(self)

    # _silence_watchdog_loop: moved to orch_watchdog.py

    def _detect_stuck(self) -> dict | None:
        """Detect if the orchestrator is stuck. Delegated to orch_watchdog."""
        return orch_watchdog.detect_stuck(self)

    def _read_project_manifest(self) -> str:
        """Read .hivemind/PROJECT_MANIFEST.md. Delegated to orch_watchdog."""
        return orch_watchdog._read_project_manifest(self)

    def _estimate_task_complexity(self, task: str) -> str:
        """Classify task complexity. Delegated to orch_watchdog."""
        return orch_watchdog.estimate_task_complexity(task)

    def _check_premature_completion(self, loop_count: int, task: str) -> str | None:
        """Validate whether TASK_COMPLETE is appropriate. Delegated to orch_watchdog."""
        return orch_watchdog.check_premature_completion(self, loop_count, task)

    async def _build_final_summary(
        self, user_message: str, start_time: float, status: str = "Done"
    ) -> str:
        """Build a clean final status message."""
        duration = time.monotonic() - start_time
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        duration_str = f"{minutes}m {seconds:02d}s" if minutes > 0 else f"{seconds}s"

        task_preview = user_message[:100]
        if len(user_message) > 100:
            task_preview += "..."

        agents_used = list(
            dict.fromkeys(m.agent_name for m in self.conversation_log if m.agent_name != "user")
        )
        # Also include any agents from _agents_used that were trimmed from conversation_log
        for a in sorted(self._agents_used - set(agents_used)):
            if a != "user":
                agents_used.append(a)
        agents_str = " → ".join(agents_used) if agents_used else "orchestrator"

        file_changes = await orch_review.detect_file_changes(self)
        changes_str = ""
        if file_changes and "(no file" not in file_changes:
            changes_str = f"\n\n📝 Changes:\n```\n{file_changes}\n```"

        # Show what was accomplished each round
        rounds_str = ""
        if self._completed_rounds:
            rounds_str = "\n\n🔄 Rounds:\n" + "\n".join(f"  {r}" for r in self._completed_rounds)

        return (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ {self.project_name} — {status}\n\n"
            f"📋 Task: {task_preview}\n"
            f"🤖 Agents: {agents_str}\n"
            f"⏱ {duration_str} | 📊 {self.turn_count} turns | 💰 ${self.total_cost_usd:.2f}"
            f"{rounds_str}"
            f"{changes_str}\n\n"
            f"Send another message to continue.\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

    def _get_workspace_context(self) -> str:
        """Scan project directory and return a short file listing (2 levels deep)."""
        entries = []
        try:
            for item in sorted(Path(self.project_dir).iterdir()):
                if item.name.startswith(".") or item.name in (
                    "__pycache__",
                    "node_modules",
                    ".git",
                    "venv",
                    ".venv",
                ):
                    continue
                if item.is_dir():
                    entries.append(f"  {item.name}/")
                    try:
                        for sub in sorted(item.iterdir()):
                            if sub.name.startswith(".") or sub.name == "__pycache__":
                                continue
                            entries.append(f"    {sub.name}{'/' if sub.is_dir() else ''}")
                            if len(entries) >= 50:
                                break
                    except PermissionError:
                        pass
                else:
                    entries.append(f"  {item.name}")
                if len(entries) >= 50:
                    entries.append("  ... (truncated)")
                    break
        except Exception as _ws_err:
            logger.debug(
                "[%s] _get_workspace_context: filesystem scan failed: %s", self.project_id, _ws_err
            )
            entries = ["  (unable to list files)"]

        if not entries:
            return ""
        return "Current workspace files:\n" + "\n".join(entries)

    async def start_session(self, user_message: str):
        """Start processing a user message.

        Routing:
        - is_multi_agent=True + USE_DAG_EXECUTOR env var → new Typed Contract / DAG system
        - is_multi_agent=True (no env var) → legacy regex-delegate system
        - is_multi_agent=False → solo mode
        """
        if self.is_running:
            # Route through the parallel ingestion queue instead of dropping.
            # This makes start_session() truly non-blocking: it returns
            # immediately and the submitted graph waits in the queue until
            # the current session finishes and the semaphore has capacity.
            logger.info(
                f"[{self.project_id}] start_session: already running — "
                f"queuing new task (queue depth will be "
                f"{self._graph_ingestion_queue.qsize() + 1})"
            )
            await self._submit_to_graph_ingestion_queue(user_message)
            return

        use_dag = self.multi_agent and USE_DAG_EXECUTOR
        logger.info(
            f"[{self.project_id}] Starting session: "
            f"multi_agent={self.multi_agent} dag={use_dag} message={user_message[:80]}"
        )
        self.is_running = True
        self._stop_event.clear()
        self._pause_event.set()
        self.turn_count = 0

        # Emit task_queued first so the frontend knows work has been accepted,
        # then immediately follow with project_status=running.
        await self._emit_event(
            "task_queued",
            message_preview=user_message[:100],
            queue_position=1,
            queue_depth=0,
            running_graphs=1,
            max_concurrent_graphs=DAG_MAX_CONCURRENT_GRAPHS,
        )
        await self._emit_event("project_status", status="running")
        # Immediate feedback so the UI shows activity right away
        self.agent_states["orchestrator"] = {
            "state": "working",
            "task": "preparing workspace...",
        }
        await self._emit_event(
            "agent_started",
            agent="orchestrator",
            task="preparing workspace...",
        )
        await self.session_mgr.invalidate_session(self.user_id, self.project_id, "orchestrator")

        # Start the agent silence watchdog (monitors for silent agents)
        self._start_silence_watchdog()
        # Ensure the graph ingestion dispatcher is running so queued tasks
        # (submitted while this session executes) are picked up automatically.
        self._ensure_graph_dispatcher()

        if use_dag:
            self._task = asyncio.create_task(self._run_dag_session(user_message))
        else:
            self._task = asyncio.create_task(self._run_orchestrator(user_message))
        self._task.add_done_callback(self._on_task_done)

    async def inject_user_message(self, agent_name: str, message: str):
        """Inject a user message into the orchestrator or a sub-agent.

        Uses an asyncio.Queue so multiple concurrent messages are never lost.
        """
        # Log user message
        self.conversation_log.append(
            Message(agent_name="user", role="User", content=f"[to {agent_name}] {message}")
        )
        self._create_background_task(
            self.session_mgr.add_message(
                self.project_id, "user", "User", f"[to {agent_name}] {message}"
            )
        )

        if not self.is_running:
            # Not running — send directly to the requested agent (or orchestrator if unknown)
            from config import get_all_role_names

            all_known = get_all_role_names(include_legacy=True)
            target = (
                agent_name
                if (agent_name in all_known or agent_name == "orchestrator")
                else "orchestrator"
            )
            await self._notify(f"📨 Sending to *{target}*...")
            response = await self._query_agent(target, message)
            self._record_response(target, target.title(), response)
            self.turn_count += 1  # Track this turn for cost/limit accounting

            summary = response.text[:3000]
            if len(response.text) > 3000:
                summary += "\n... (truncated)"
            cost_str = f" | ${response.cost_usd:.4f}" if response.cost_usd > 0 else ""
            await self._send_final(f"💬 *{target}*{cost_str}\n\n{summary}")
        else:
            # Enqueue — the orchestrator loop / _on_task_done will drain pending messages
            await self._message_queue.put((agent_name, message))
            queue_size = self._message_queue.qsize()
            logger.info(
                f"[{self.project_id}] Queued message for '{agent_name}' "
                f"(queue size: {queue_size}) — lifecycle: enqueued"
            )
            # Notify the user that their message is queued (not lost)
            await self._send_result(
                f"\U0001f4ec **Message queued** (position #{queue_size})\n"
                f"_Your message will be processed after the current task completes._\n"
                f"> {message[:200]}{'...' if len(message) > 200 else ''}"
            )
            # Emit event so frontend can show queue indicator
            await self._emit_event(
                "message_queued",
                queue_size=queue_size,
                message_preview=message[:100],
            )
            if self.is_paused:
                self.resume()

    def pause(self):
        """Pause the orchestration loop (agents finish current work)."""
        if self.is_running and not self.is_paused:
            self.is_paused = True
            self._pause_event.clear()
            logger.info("Session paused")

    async def _self_pause(self, reason: str = "paused"):
        """Pause from within the orchestrator loop — emits project_status so frontend updates."""
        self.is_paused = True
        self._pause_event.clear()
        logger.info(f"[{self.project_id}] Self-paused: {reason}")
        await self._emit_event("project_status", status="paused", reason=reason)

    def resume(self):
        """Resume a paused orchestration loop."""
        if self.is_paused:
            self.is_paused = False
            self._pause_event.set()
            logger.info("Session resumed")

    async def stop(self):
        """Gracefully stop the orchestration session."""
        import traceback

        caller = "".join(traceback.format_stack(limit=4))
        logger.info(f"[{self.project_id}] stop() called. Caller:\n{caller}")
        self._stop_event.set()
        self.is_running = False
        self.is_paused = False
        self._pause_event.set()
        self._approval_event.set()  # Unblock any pending approval

        # Cancel the agent silence watchdog
        await self._stop_silence_watchdog()

        # Cancel the graph ingestion dispatcher (stops picking up queued tasks)
        if self._graph_dispatcher and not self._graph_dispatcher.done():
            self._graph_dispatcher.cancel()
            try:
                await self._graph_dispatcher
            except asyncio.CancelledError:
                pass
            self._graph_dispatcher = None

        # Drain any pending graph ingestion queue entries
        _drained_graphs = 0
        while True:
            try:
                self._graph_ingestion_queue.get_nowait()
                _drained_graphs += 1
            except asyncio.QueueEmpty:
                break
        if _drained_graphs:
            logger.info(
                f"[{self.project_id}] Drained {_drained_graphs} pending graph(s) from ingestion queue on stop"
            )

        # Cancel main orchestrator task
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Wait for background tasks to finish (with timeout)
        if self._background_tasks:
            logger.info(
                f"[{self.project_id}] Waiting for {len(self._background_tasks)} background tasks..."
            )
            pending = list(self._background_tasks)
            _done, still_pending = await asyncio.wait(pending, timeout=ASYNC_WAIT_TIMEOUT)
            for t in still_pending:
                t.cancel()
            if still_pending:
                logger.warning(
                    f"[{self.project_id}] Cancelled {len(still_pending)} stuck background tasks"
                )

        # Drain any remaining queued messages atomically (get_nowait loop is
        # TOCTOU-safe; the prior `while not queue.empty()` pattern had a race
        # window where another coroutine could drain between empty() and get_nowait()).
        drained = 0
        while True:
            try:
                self._message_queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            logger.info(f"[{self.project_id}] Drained {drained} queued messages on stop")

        self._active_graph_count = 0

        await self._send_final(
            f"🛑 Project *{self.project_name}* stopped.\n"
            f"📊 Turns: {self.turn_count} | 💰 Cost: ${self.total_cost_usd:.4f}"
        )

    async def request_approval(self, description: str) -> bool:
        """Request human approval before proceeding. Blocks until approved/rejected."""
        self._pending_approval = description
        self._approval_event.clear()
        self._approval_result = True

        await self._emit_event(
            "approval_request",
            description=description,
        )
        await self._notify(f"⏸️ Approval needed: {description}")

        # Wait for approval or stop
        await self._approval_event.wait()
        self._pending_approval = None
        return self._approval_result

    # ── Parallel Task Ingestion ─────────────────────────────────────────────
    # These methods implement the bounded worker-pool for concurrent graph
    # execution.  They are called by start_session() and the auto-restart
    # logic in _on_task_done().

    async def _submit_to_graph_ingestion_queue(self, user_message: str) -> None:
        """Queue a new task graph for execution and emit 'task_queued' event.

        This is the entry point for non-blocking task submission.  It returns
        immediately — the submitted graph will execute as soon as:
          1. The current session finishes (is_running becomes False), AND
          2. A semaphore slot is available (active graphs < DAG_MAX_CONCURRENT_GRAPHS).

        Args:
            user_message: The user's message / task description.
        """
        queue_depth = self._graph_ingestion_queue.qsize()
        queue_position = queue_depth + 1  # 1-indexed position in the queue

        # Put the message in the queue (always non-blocking)
        await self._graph_ingestion_queue.put(user_message)

        logger.info(
            f"[{self.project_id}] Task queued for parallel ingestion "
            f"(position={queue_position}, depth={queue_depth + 1}, "
            f"active_graphs={self._active_graph_count})"
        )

        # Emit task_queued event — frontend can show a "Task queued" indicator
        await self._emit_event(
            "task_queued",
            message_preview=user_message[:100],
            queue_position=queue_position,
            queue_depth=queue_depth + 1,
            running_graphs=self._active_graph_count,
            max_concurrent_graphs=DAG_MAX_CONCURRENT_GRAPHS,
        )

        # Notify user so they know their submission was accepted
        await self._send_result(
            f"📋 **Task queued** (position #{queue_position})\n"
            f"_Your request will start when the current task completes._\n"
            f"> {user_message[:200]}{'...' if len(user_message) > 200 else ''}"
        )

        # Ensure the dispatcher is running to pick up this new entry
        self._ensure_graph_dispatcher()

    def _ensure_graph_dispatcher(self) -> None:
        """Start the graph ingestion dispatcher if it is not already running.

        The dispatcher is a long-lived background task that drains
        ``_graph_ingestion_queue`` and starts graph executions as semaphore
        slots become available.  It is idempotent — calling it when the
        dispatcher is already running is a no-op.
        """
        if self._graph_dispatcher is not None and not self._graph_dispatcher.done():
            return  # Already running

        self._graph_dispatcher = asyncio.create_task(
            self._graph_dispatch_loop(),
            name=f"graph-dispatcher-{self.project_id}",
        )

        def _on_dispatcher_done(t: asyncio.Task[None]) -> None:
            if not t.cancelled():
                exc = t.exception()
                if exc:
                    logger.error(
                        f"[{self.project_id}] Graph dispatcher crashed unexpectedly: {exc}",
                        exc_info=exc,
                    )

        self._graph_dispatcher.add_done_callback(_on_dispatcher_done)
        logger.debug(f"[{self.project_id}] Graph ingestion dispatcher started")

    async def _graph_dispatch_loop(self) -> None:
        """Drain the graph ingestion queue, launching sessions under the semaphore.

        Design:
        - Polls the queue with a 1-second timeout so it wakes up quickly when
          new graphs are submitted but doesn't spin-wait when idle.
        - Each graph execution runs under ``_graph_semaphore``, so at most
          ``DAG_MAX_CONCURRENT_GRAPHS`` sessions run simultaneously.
        - The loop exits cleanly on CancelledError (raised by ``stop()``).
        """
        logger.debug(f"[{self.project_id}] Graph dispatch loop starting")
        while True:
            try:
                try:
                    user_message = await asyncio.wait_for(
                        self._graph_ingestion_queue.get(),
                        timeout=1.0,
                    )
                except TimeoutError:
                    # Nothing in queue — keep looping (check for new items)
                    continue

                # Acquire semaphore slot before starting execution.
                # If all slots are occupied this coroutine waits here without
                # blocking the event loop (cooperative asyncio suspension).
                logger.info(
                    f"[{self.project_id}] Dispatcher: got queued message, "
                    f"waiting for semaphore slot "
                    f"(active={self._active_graph_count}, max={DAG_MAX_CONCURRENT_GRAPHS})"
                )
                # Launch under semaphore as a separate task so the dispatch
                # loop can continue receiving new queue items while slots fill.
                _graph_task = asyncio.create_task(
                    self._run_queued_graph(user_message),
                    name=f"queued-graph-{self.project_id}",
                )
                self._background_tasks.add(_graph_task)
                _graph_task.add_done_callback(self._background_tasks.discard)

            except asyncio.CancelledError:
                logger.debug(f"[{self.project_id}] Graph dispatch loop cancelled — exiting")
                break
            except Exception as exc:
                # Dispatcher must never crash — log and continue
                logger.error(
                    f"[{self.project_id}] Graph dispatch loop error (will continue): {exc}",
                    exc_info=True,
                )

    async def _run_queued_graph(self, user_message: str) -> None:
        """Execute one queued graph under the concurrency semaphore.

        Acquires a semaphore slot before calling ``start_session()``, and
        releases it when the session finishes.  This guarantees that at most
        ``DAG_MAX_CONCURRENT_GRAPHS`` sessions run in parallel across ALL
        queued entries for this orchestrator.

        Note: ``start_session()`` sets ``self.is_running = True`` during
        execution.  For parallel execution (``DAG_MAX_CONCURRENT_GRAPHS > 1``)
        the caller must be aware that ``is_running`` may already be True if
        another session is in progress.  In the default single-project scenario
        (``DAG_MAX_CONCURRENT_GRAPHS=1``) sessions execute strictly serially.
        """
        async with self._graph_semaphore:
            self._active_graph_count += 1
            logger.info(
                f"[{self.project_id}] Starting queued graph "
                f"(active_graphs={self._active_graph_count}): "
                f"{user_message[:80]}"
            )
            try:
                # Wait for any current running session to finish before starting
                # (respects the existing is_running state guard in start_session).
                _wait_attempts = 0
                while self.is_running:
                    _wait_attempts += 1
                    if _wait_attempts % 30 == 1:  # Log every 30s
                        logger.info(
                            f"[{self.project_id}] Queued graph waiting for "
                            f"current session to finish (waited ~{_wait_attempts}s)"
                        )
                    await asyncio.sleep(1.0)

                # Stop event may have been set during the wait — abort if so
                if self._stop_event.is_set():
                    logger.info(f"[{self.project_id}] Queued graph aborted — stop event set")
                    return

                await self.start_session(user_message)

            except asyncio.CancelledError:
                logger.info(
                    f"[{self.project_id}] Queued graph execution cancelled: {user_message[:60]}"
                )
                raise
            except Exception as exc:
                logger.error(
                    f"[{self.project_id}] Queued graph execution failed: {exc}",
                    exc_info=True,
                )
            finally:
                self._active_graph_count = max(0, self._active_graph_count - 1)
                logger.info(
                    f"[{self.project_id}] Queued graph finished "
                    f"(remaining_active={self._active_graph_count})"
                )

    def approve(self):
        """Approve the pending request."""
        self._approval_result = True
        self._approval_event.set()

    def reject(self):
        """Reject the pending request."""
        self._approval_result = False
        self._approval_event.set()

    @property
    def pending_approval(self) -> str | None:
        """Return the pending human-approval request, if any."""
        return self._pending_approval

    @property
    def pending_message_count(self) -> int:
        """Return the number of pending messages in the queue.

        Public API for external consumers (state writer, dashboard API)
        instead of accessing the private ``_message_queue`` directly.
        """
        return self._message_queue.qsize()

    def drain_message_queue(self) -> int:
        """Drain all pending messages from the queue.

        Returns the number of messages drained. Used during history clear
        to ensure no stale messages are processed after a reset.
        """
        count = 0
        while not self._message_queue.empty():
            try:
                self._message_queue.get_nowait()
                count += 1
            except asyncio.QueueEmpty:
                break
        return count

    # --- DAG-based session (new Typed Contract system) ---

    async def _load_project_context(self) -> tuple[str, str, str]:
        """Load manifest, memory snapshot, and file tree. Each is individually guarded."""
        manifest = ""
        try:
            manifest = await self._load_manifest()
        except Exception as e:
            logger.warning(f"[{self.project_id}] _load_manifest failed (non-fatal): {e}")

        memory_snapshot = ""
        try:
            memory_snapshot = await self._load_memory_snapshot()
        except Exception as e:
            logger.warning(f"[{self.project_id}] _load_memory_snapshot failed (non-fatal): {e}")

        file_tree = ""
        try:
            file_tree = await asyncio.to_thread(self._list_workspace_files)
        except Exception as e:
            logger.warning(f"[{self.project_id}] _list_workspace_files failed (non-fatal): {e}")

        return manifest, memory_snapshot, file_tree

    async def _run_dag_session(self, user_message: str, _retry_count: int = 0, _cached_graph=None):
        """
        Execution path using the Typed Contract Protocol v2:

        1. Load Memory  → read existing MemorySnapshot for context continuity
        2. PM Agent     → creates TaskGraph (structured, typed, artifact-aware)
        3. DAG Executor → runs tasks with self-healing, artifact passing, smart retry
        4. Memory Agent → updates project memory from all task outputs
        5. Summary      → returned to user as final output

        Falls back to legacy _run_orchestrator if PM fails.

        FIX(C-4): Uses an internal retry loop instead of call_later + new
        task creation.  This eliminates the race condition between stop()
        and retry spawning — stop() can cancel the SAME task instead of
        racing with a callback that creates a new one.
        """
        # Lazy imports to avoid circular dependency
        from dag_executor import ExecutionResult, build_execution_summary, execute_graph
        from memory_agent import update_project_memory
        from pm_agent import create_task_graph, fallback_single_task_graph

        _anyio_retries = _retry_count
        _last_graph = _cached_graph  # Preserve graph across retries

        _dag_exit_reason = "unknown"  # Track how the DAG session exited
        _should_retry = False

        # If this is a retry, drain any residual cancellations and log.
        if _retry_count > 0:
            _residual = self._drain_cancellations()
            logger.info(
                f"[{self.project_id}] DAG retry #{_retry_count} starting "
                f"(residual_cancellations_drained={_residual})"
            )
            # Reset DAG task statuses for the retry
            self._dag_task_statuses = {}

        try:
            # Ensure .hivemind/ directory exists for memory persistence
            forge_dir = Path(self.project_dir) / ".hivemind"
            forge_dir.mkdir(parents=True, exist_ok=True)

            # ── Step 0: Load project context ──
            self.agent_states["orchestrator"] = {
                "state": "working",
                "task": "loading project context...",
                "current_tool": "reading memory, manifest, file tree",
            }
            await self._emit_event(
                "agent_update",
                agent="orchestrator",
                summary="Loading project context (memory, manifest, file tree)...",
                text="loading project context...",
            )
            await self._send_result("🧠 **Orchestrator** loading project context...")
            manifest, memory_snapshot, file_tree = await self._load_project_context()
            logger.info(
                f"[{self.project_id}] Context loaded: "
                f"manifest={'yes' if manifest else 'no'} ({len(manifest)} chars), "
                f"memory={'yes' if memory_snapshot else 'no'} ({len(memory_snapshot)} chars), "
                f"file_tree={'yes' if file_tree else 'no'} ({len(file_tree)} chars)"
            )

            # ── Proactive Memory: inject lessons learned from past tasks ──
            lessons_learned = ""
            try:
                from memory_agent import get_lessons_learned

                lessons_learned = await asyncio.to_thread(
                    get_lessons_learned, self.project_dir, user_message
                )
                if lessons_learned:
                    logger.info(
                        f"[{self.project_id}] Injecting lessons learned "
                        f"({len(lessons_learned)} chars) from past executions"
                    )
                    # Append lessons to memory_snapshot so PM agent sees them
                    memory_snapshot = (
                        (memory_snapshot + "\n\n" + lessons_learned).strip()
                        if memory_snapshot
                        else lessons_learned
                    )
            except Exception as lessons_err:
                logger.debug(
                    f"[{self.project_id}] get_lessons_learned failed (non-fatal): {lessons_err}"
                )

            context_parts = []
            if memory_snapshot:
                context_parts.append("project memory")
            if manifest:
                context_parts.append("manifest")
            if file_tree:
                context_parts.append(f"file tree ({file_tree.count(chr(10))} files)")
            if context_parts:
                await self._send_result(f"📚 Loaded: {', '.join(context_parts)}")

            # ── Step 0.5: Architect Agent (pre-planning review) ──
            architect_brief: ArchitectureBrief | None = None
            if _cached_graph is None and should_run_architect(user_message, bool(memory_snapshot)):
                self.agent_states["orchestrator"] = {
                    "state": "working",
                    "task": "Architect reviewing codebase...",
                    "current_tool": "Architect Agent — analysing architecture",
                }
                await self._emit_event(
                    "agent_update",
                    agent="orchestrator",
                    summary="Architect Agent is reviewing the codebase before planning...",
                    text="Architect reviewing codebase...",
                )
                await self._send_result(
                    "🏗️ **Architect Agent** is reviewing the codebase architecture before planning..."
                )
                try:
                    architect_brief = await run_architect_review(
                        project_dir=self.project_dir,
                        project_id=self.project_id,
                        user_task=user_message,
                        memory_snapshot={"memory": memory_snapshot} if memory_snapshot else None,
                    )
                    if architect_brief and architect_brief.codebase_summary:
                        # Inject architect brief into memory_snapshot for PM
                        _key_files_str = (
                            "\n".join(
                                f"  {fp}: {desc}" for fp, desc in architect_brief.key_files.items()
                            )
                            if architect_brief.key_files
                            else "(none identified)"
                        )
                        arch_context = (
                            f"\n\n<architect_brief>\n"
                            f"Codebase: {architect_brief.codebase_summary}\n"
                            f"Tech stack: {architect_brief.tech_stack}\n"
                            f"Patterns: {', '.join(architect_brief.architecture_patterns)}\n"
                            f"Key files:\n{_key_files_str}\n"
                            f"Constraints: {'; '.join(architect_brief.constraints)}\n"
                            f"Risks: {'; '.join(architect_brief.risks)}\n"
                            f"Recommended approach: {architect_brief.recommended_approach}\n"
                            f"Parallelism hints: {'; '.join(architect_brief.parallelism_hints)}\n"
                            f"</architect_brief>"
                        )
                        memory_snapshot = (
                            (memory_snapshot + arch_context)
                            if memory_snapshot
                            else arch_context.strip()
                        )
                        await self._send_result(
                            f"🏗️ **Architect brief ready:** {len(architect_brief.key_files)} key files, "
                            f"{len(architect_brief.risks)} risks identified, "
                            f"stack: {', '.join(f'{k}: {v}' for k, v in list(architect_brief.tech_stack.items())[:3])}"
                        )
                        logger.info(
                            f"[{self.project_id}] Architect brief: "
                            f"{len(architect_brief.key_files)} files, "
                            f"{len(architect_brief.risks)} risks, "
                            f"{len(architect_brief.constraints)} constraints"
                        )
                    else:
                        logger.info(
                            f"[{self.project_id}] Architect returned empty brief — skipping"
                        )
                except Exception as arch_err:
                    logger.warning(
                        f"[{self.project_id}] Architect Agent failed (non-fatal): {arch_err}"
                    )
                    await self._send_result(
                        "⚠️ Architect review skipped — continuing with planning..."
                    )

            # ── Step 0.6: Cross-Project Memory injection ──
            cross_memory_context = ""
            try:
                _xmem_dir = Path.home() / ".hivemind" / "global"
                _xmem = CrossProjectMemory(_xmem_dir)
                # Extract tech stack hints from architect brief or file tree
                _tech_hints = []
                if architect_brief and architect_brief.tech_stack:
                    _tech_hints = list(architect_brief.tech_stack.values())
                cross_memory_context = _xmem.build_context_for_task(
                    task=user_message,
                    tech_stack=_tech_hints if _tech_hints else None,
                )
                if cross_memory_context:
                    memory_snapshot = (
                        (memory_snapshot + "\n\n" + cross_memory_context).strip()
                        if memory_snapshot
                        else cross_memory_context
                    )
                    logger.info(
                        f"[{self.project_id}] Cross-project memory injected "
                        f"({len(cross_memory_context)} chars)"
                    )
            except Exception as xmem_err:
                logger.debug(
                    f"[{self.project_id}] Cross-project memory failed (non-fatal): {xmem_err}"
                )

            # ── Step 1: PM creates the plan (skip on retry — reuse cached graph) ──
            import time as _pm_time

            if _cached_graph is not None:
                graph = _cached_graph
                logger.info(
                    f"[{self.project_id}] DAG retry #{_retry_count}: reusing cached task graph "
                    f"({len(graph.tasks)} tasks) — skipping PM Agent"
                )
                await self._emit_event(
                    "agent_update",
                    agent="orchestrator",
                    summary=f"Retrying DAG execution (attempt {_retry_count}) with existing plan...",
                    text=f"retrying with existing plan ({len(graph.tasks)} tasks)...",
                )
                await self._send_result(
                    f"🔄 **Retrying** DAG execution (attempt {_retry_count}) with existing plan "
                    f"({len(graph.tasks)} tasks)..."
                )
                # Re-emit the graph so frontend shows it on retry
                await self._emit_event("task_graph", graph=graph.model_dump())
            else:
                self.agent_states["orchestrator"] = {
                    "state": "working",
                    "task": "PM creating execution plan...",
                    "current_tool": "PM Agent analyzing request",
                }
                await self._emit_event(
                    "agent_update",
                    agent="orchestrator",
                    summary="PM Agent is creating the execution plan...",
                    text="PM creating execution plan...",
                )
                await self._send_result(
                    f"🗺️ **PM Agent** is analyzing your request and creating an execution plan...\n"
                    f"_Request: {user_message[:150]}{'...' if len(user_message) > 150 else ''}_"
                )

                # Step 1: PM Agent → TaskGraph (now with memory context)
                _pm_start = _pm_time.time()
                logger.info(f"[{self.project_id}] PM Agent starting task graph creation...")
                try:
                    graph = await create_task_graph(
                        user_message=user_message,
                        project_id=self.project_id,
                        manifest=manifest,
                        file_tree=file_tree,
                        memory_snapshot=memory_snapshot,
                    )
                    _pm_elapsed = _pm_time.time() - _pm_start
                    logger.info(
                        f"[{self.project_id}] PM Agent completed in {_pm_elapsed:.1f}s: "
                        f"{len(graph.tasks)} tasks, {len(graph.epic_breakdown)} epics, "
                        f"vision='{graph.vision[:80]}'"
                    )
                    for t in graph.tasks:
                        deps = (
                            f" (deps: {', '.join(t.depends_on)})" if t.depends_on else " (no deps)"
                        )
                        logger.info(f"  Task {t.id}: {t.role.value} — {t.goal[:80]}{deps}")
                except Exception as pm_err:
                    _pm_elapsed = _pm_time.time() - _pm_start
                    logger.warning(
                        f"[{self.project_id}] PM Agent failed after {_pm_elapsed:.1f}s: {pm_err}. Using fallback."
                    )
                    graph = fallback_single_task_graph(user_message, self.project_id)

                # --- Critic: validate graph quality before execution ---
                # This is the Evaluator half of the Evaluator-Optimizer pattern.
                # Quality issues are logged but don't block execution.
                try:
                    from pm_agent import validate_graph_quality

                    quality_issues = validate_graph_quality(graph)
                    if quality_issues:
                        error_count = sum(1 for i in quality_issues if i.startswith("ERROR"))
                        warn_count = sum(1 for i in quality_issues if i.startswith("WARNING"))
                        logger.info(
                            f"[{self.project_id}] Graph quality check: "
                            f"{error_count} errors, {warn_count} warnings, "
                            f"{len(quality_issues)} total issues"
                        )
                        for issue in quality_issues:
                            if issue.startswith("ERROR"):
                                logger.error(f"[{self.project_id}] Graph quality: {issue}")
                            elif issue.startswith("WARNING"):
                                logger.warning(f"[{self.project_id}] Graph quality: {issue}")
                            else:
                                logger.debug(f"[{self.project_id}] Graph quality: {issue}")
                    else:
                        logger.info(f"[{self.project_id}] Graph quality check: passed (no issues)")
                except Exception as critic_err:
                    logger.debug(
                        f"[{self.project_id}] Graph critic failed (non-fatal): {critic_err}"
                    )
                    quality_issues = []

                # Surface critic results to the user in chat
                if quality_issues:
                    error_lines = [i for i in quality_issues if i.startswith("ERROR")]
                    warn_lines = [i for i in quality_issues if i.startswith("WARNING")]
                    parts = []
                    if error_lines:
                        parts.append(
                            "**Errors:** " + " | ".join(e[6:].strip() for e in error_lines)
                        )
                    if warn_lines:
                        parts.append(
                            "**Warnings:** " + " | ".join(w[8:].strip() for w in warn_lines)
                        )
                    await self._send_result(
                        f"🔍 **Plan check:** {len(quality_issues)} issue(s) found\n"
                        + "\n".join(parts)
                    )
                else:
                    await self._send_result("✅ **Plan check:** passed — no issues found")

                # Report the plan — show each task so the user sees what's coming
                task_lines = []
                for i, t in enumerate(graph.tasks, 1):
                    deps = f" (after: {', '.join(t.depends_on)})" if t.depends_on else ""
                    task_lines.append(f"  {i}. **{t.role.value}** — {t.goal[:80]}{deps}")
                plan_detail = chr(10).join(task_lines)
                await self._send_result(
                    f"📋 **Plan ready:** {graph.vision}\n\n"
                    f"{plan_detail}\n\n"
                    f"_Total: {len(graph.tasks)} tasks, {len(graph.epic_breakdown)} epics_"
                )

                # Emit the full graph to the frontend for visualization
                await self._emit_event("task_graph", graph=graph.model_dump())

            # Cache the graph for potential retry (before execute_graph which may fail)
            _last_graph = graph

            # ── Interactive mode: show plan and wait for user confirmation ──
            # In "interactive" mode, surface the execution plan as a question so
            # the user can approve, adjust, or redirect before agents run.
            if self.mode == "interactive" and _cached_graph is None:
                task_lines_confirm = []
                for i, t in enumerate(graph.tasks, 1):
                    deps = f" (after: {', '.join(t.depends_on)})" if t.depends_on else ""
                    task_lines_confirm.append(f"  {i}. **{t.role.value}** — {t.goal[:80]}{deps}")
                plan_summary = chr(10).join(task_lines_confirm)
                confirm_question = (
                    f"Here is my plan ({len(graph.tasks)} tasks):\n\n"
                    f"{plan_summary}\n\n"
                    "Should I proceed with this plan, or would you like to adjust anything?"
                )
                logger.info(
                    "[%s] Interactive mode: surfacing plan for user confirmation",
                    self.project_id,
                )
                user_reply = await self._pause_for_question(confirm_question)
                if user_reply is None:
                    # Session was stopped while waiting
                    return
                # If the user just says go ahead / yes, proceed; otherwise their
                # reply becomes extra context (they may have changed the task).
                _go_ahead_phrases = (
                    "go ahead",
                    "yes",
                    "ok",
                    "proceed",
                    "looks good",
                    "continue",
                    "sure",
                )
                if not any(p in user_reply.strip().lower() for p in _go_ahead_phrases):
                    # User provided corrections — append to user_message and re-plan
                    logger.info(
                        "[%s] Interactive mode: user adjusted plan — re-running PM with corrections",
                        self.project_id,
                    )
                    amended_message = f"{user_message}\n\n[User correction]: {user_reply}"
                    try:
                        graph = await create_task_graph(
                            user_message=amended_message,
                            project_id=self.project_id,
                            manifest=manifest,
                            file_tree=file_tree,
                            memory_snapshot=memory_snapshot,
                        )
                        _last_graph = graph
                        # Re-emit updated graph
                        await self._emit_event("task_graph", graph=graph.model_dump())
                        await self._send_result(
                            f"Updated plan ({len(graph.tasks)} tasks) based on your feedback — proceeding."
                        )
                    except Exception as replan_err:
                        logger.warning(
                            "[%s] Interactive mode: re-plan failed (%s) — using original graph",
                            self.project_id,
                            replan_err,
                        )

            # ── Pre-task question detection (autonomous mode) ────────────────
            # If the orchestrator's planning phase produced a question before
            # the first real task dispatch, surface it to the user and wait.
            # This is only relevant for the very first graph (not retries).
            if self.mode == "autonomous" and _cached_graph is None:
                # Check whether the vision or the first task goal contains a question
                _planning_text = graph.vision
                if _planning_text and self._has_question(_planning_text):
                    logger.info(
                        "[%s] Pre-task question detected in plan vision — pausing",
                        self.project_id,
                    )
                    user_reply = await self._pause_for_question(_planning_text)
                    if user_reply is None:
                        return  # Session stopped while waiting

            # Update orchestrator state — now executing the DAG
            self.agent_states["orchestrator"] = {
                "state": "working",
                "task": f"executing {len(graph.tasks)} tasks...",
                "current_tool": "DAG Executor",
            }
            await self._emit_event(
                "agent_update",
                agent="orchestrator",
                summary=f"Executing DAG: {len(graph.tasks)} tasks across agents...",
                text=f"executing {len(graph.tasks)} tasks...",
            )

            # Cache serialized graph for /live recovery endpoint (cross-tab/refresh)
            self._current_dag_graph = graph.model_dump()
            self._dag_task_statuses = {}

            # Session IDs shared across tasks (agent resume)
            session_id_store: dict[str, str] = {}

            # ── Step 1.5: Debate Engine — structured review for critical tasks ──
            try:
                from debate_engine import DebateEngine

                _debate = DebateEngine()
                for task in graph.tasks:
                    if _debate.should_debate(task):
                        logger.info(
                            f"[{self.project_id}] Debate triggered for task {task.id} ({task.role.value})"
                        )
                        await self._notify(
                            f"🗣️ **Debate Engine** reviewing critical task: {task.goal[:80]}..."
                        )
                        debate_result = await _debate.run_debate(
                            task=task,
                            project_dir=self.project_dir,
                            sdk=self.sdk,
                            context=graph.vision,
                        )
                        if debate_result and debate_result.merged_approach:
                            # Enrich the task goal with debate insights
                            task.goal = (
                                task.goal + "\n\n" + _debate.build_debate_context(debate_result)
                            )
                            logger.info(f"[{self.project_id}] Debate enriched goal for {task.id}")
            except Exception as debate_err:
                logger.warning(
                    f"[{self.project_id}] Debate Engine failed (non-fatal): {debate_err}"
                )

            # Step 2: DAG Executor
            logger.info(
                f"[{self.project_id}] Starting DAG execution: "
                f"{len(graph.tasks)} tasks, budget=${self._effective_budget:.2f}"
            )
            _dag_start = _pm_time.time()
            # In interactive mode, wire the commit approval callback so the
            # user can approve/reject each commit before it lands.
            _commit_cb = None
            if self.mode == "interactive":
                _commit_cb = self.request_approval

            result: ExecutionResult = await execute_graph(
                graph=graph,
                project_dir=self.project_dir,
                specialist_prompts=SPECIALIST_PROMPTS,
                sdk_client=self.sdk,
                on_task_start=self._on_dag_task_start,
                on_task_done=self._on_dag_task_done,
                on_remediation=self._on_dag_remediation,
                on_agent_stream=self._on_dag_agent_stream,
                on_agent_tool_use=self._on_dag_agent_tool_use,
                on_event=self.on_event,
                max_budget_usd=self._effective_budget,
                session_id_store=session_id_store,
                commit_approval_callback=_commit_cb,
            )

            _dag_elapsed = _pm_time.time() - _dag_start
            logger.info(
                f"[{self.project_id}] DAG execution completed in {_dag_elapsed:.1f}s: "
                f"{len(result.outputs)} outputs, ${result.total_cost:.4f} total cost, "
                f"{result.remediation_count} remediations"
            )
            for o in result.outputs:
                logger.info(
                    f"  Output {o.task_id}: status={o.status.value}, "
                    f"confidence={o.confidence:.2f}, turns={o.turns_used}, "
                    f"${o.cost_usd:.4f}, "
                    f"failure={'(' + (o.failure_category.value if o.failure_category else 'none') + ') ' + o.failure_details[:80] if not o.is_successful() else 'none'}"
                )

            # Step 3: Memory Agent — update project knowledge
            try:
                await self._notify("🧠 **Memory Agent** is updating project knowledge...")
                await update_project_memory(
                    project_dir=self.project_dir,
                    project_id=self.project_id,
                    graph=graph,
                    outputs=result.outputs,
                    use_llm=len(result.outputs) >= 3,
                )
                await self._notify("📝 Project memory updated successfully.")
                # Cross-project memory: extract lessons from this execution
                try:
                    _xmem_dir = Path.home() / ".hivemind" / "global"
                    _xmem = CrossProjectMemory(_xmem_dir)
                    _tech_hints = []
                    if architect_brief and architect_brief.tech_stack:
                        _tech_hints = list(architect_brief.tech_stack.values())
                    _outputs_dicts = [
                        {"status": o.status.value, "summary": o.summary, "issues": o.issues}
                        for o in result.outputs
                    ]
                    _lessons_count = _xmem.extract_lessons_from_outputs(
                        project_id=self.project_id,
                        outputs=_outputs_dicts,
                        tech_stack=_tech_hints,
                    )
                    if _lessons_count > 0:
                        logger.info(
                            f"[{self.project_id}] Cross-project memory: "
                            f"extracted {_lessons_count} lessons"
                        )
                except Exception as xmem_learn_err:
                    logger.debug(
                        f"[{self.project_id}] Cross-project lesson extraction "
                        f"failed (non-fatal): {xmem_learn_err}"
                    )
            except Exception as mem_err:
                logger.warning(f"[{self.project_id}] Memory Agent failed (non-fatal): {mem_err}")

            # Step 4: Final summary with healing history
            summary = build_execution_summary(graph, result)
            if result.healing_history:
                summary += f"\n\n🔧 **Self-healing activated:** {result.remediation_count} auto-fixes applied."

            # Count successes and failures
            ok_count = sum(1 for o in result.outputs if o.is_successful())
            fail_count = len(result.outputs) - ok_count
            if fail_count > 0:
                # List failure reasons
                fail_lines = []
                for o in result.outputs:
                    if not o.is_successful():
                        reason = (
                            o.failure_details[:100]
                            if o.failure_details
                            else (o.failure_category.value if o.failure_category else "unknown")
                        )
                        fail_lines.append(f"  - `{o.task_id}`: {reason}")
                summary += f"\n\n⚠️ **{fail_count} task(s) failed:**\n" + "\n".join(fail_lines)

            summary += "\n\n---\n💬 Send another message to continue working on this project."
            await self._send_final(summary)

            # Record outputs in conversation log for persistence
            for output in result.outputs:
                self._record_dag_output(output)

            # Update total cost
            self.total_cost_usd += result.total_cost
            _dag_exit_reason = "normal"  # Reached end of try block = success

        except asyncio.CancelledError:
            # ── Distinguish REAL cancellation from SPURIOUS anyio leak ──
            # The Claude SDK uses anyio internally, which has a known bug where
            # CancelledError leaks from cancel-scope cleanup when an async
            # generator is GC'd in a different task. If _stop_event is NOT set,
            # this is a spurious cancellation — we should retry, not give up.
            if self._stop_event.is_set():
                # Real cancellation (user pressed Stop or system shutdown)
                logger.info(f"[{self.project_id}] DAG session cancelled by user (stop_event set)")
                _dag_exit_reason = "cancelled"
                # Log agent state snapshot at cancellation for debugging
                _working_agents = [
                    f"{name}(task={st.get('task', '?')[:60]}, cost=${st.get('cost', 0):.4f}, turns={st.get('turns', 0)})"
                    for name, st in self.agent_states.items()
                    if st.get("state") == "working"
                ]
                _working_tasks = [
                    f"{tid}={tstat}"
                    for tid, tstat in self._dag_task_statuses.items()
                    if tstat == "working"
                ]
                logger.info(
                    f"[{self.project_id}] DAG cancellation state dump:\n"
                    f"  Working agents: {_working_agents or 'none'}\n"
                    f"  Working tasks: {_working_tasks or 'none'}\n"
                    f"  Total cost so far: ${self.total_cost_usd:.4f}\n"
                    f"  Turn count: {self.turn_count}"
                )
            else:
                # SPURIOUS — anyio cancel-scope leak. Drain ALL cancellations and retry.
                ct = asyncio.current_task()
                _cancel_count = ct.cancelling() if ct and hasattr(ct, "cancelling") else "?"
                _drained = self._drain_cancellations()
                _anyio_retries += 1
                logger.warning(
                    f"[{self.project_id}] DAG session got SPURIOUS CancelledError "
                    f"(stop_event NOT set — anyio cancel-scope leak). "
                    f"Retry {_anyio_retries}/{MAX_ANYIO_RETRIES}. "
                    f"cancelling_count_before={_cancel_count}, drained={_drained}, "
                    f"cancelling_count_after={ct.cancelling() if ct and hasattr(ct, 'cancelling') else '?'}"
                )
                await self._emit_event(
                    "agent_update",
                    agent="orchestrator",
                    summary=f"Internal hiccup (anyio bug) — retrying automatically (attempt {_anyio_retries})...",
                    text="retrying after spurious cancellation...",
                )
                if _anyio_retries <= MAX_ANYIO_RETRIES:
                    _should_retry = True
                    _dag_exit_reason = "normal"  # Don't mark as abnormal — we're retrying
                else:
                    logger.error(
                        f"[{self.project_id}] DAG session: too many spurious CancelledErrors "
                        f"({_anyio_retries}x). Giving up."
                    )
                    _dag_exit_reason = f"cancelled_spurious_exhausted_{_anyio_retries}x"
                    await self._send_final(
                        f"\u26a0\ufe0f **{self.project_name}** — Repeated internal errors ({_anyio_retries}x).\n"
                        f"Send your message again to retry.\n"
                        f"\ud83d\udcca Turns: {self.turn_count} | \ud83d\udcb0 ${self.total_cost_usd:.4f}"
                    )
        except Exception as exc:
            _dag_exit_reason = f"error: {type(exc).__name__}: {str(exc)[:200]}"
            # ── Comprehensive crash logging ──
            # Log the full exception with stack trace
            logger.exception(
                f"[{self.project_id}] DAG session FATAL error: {type(exc).__name__}: {exc}"
            )
            # Log the full state snapshot at crash time for post-mortem debugging
            _working_agents = [
                f"{name}(task={st.get('task', '?')[:60]}, cost=${st.get('cost', 0):.4f}, turns={st.get('turns', 0)}, duration={st.get('duration', 0):.1f}s)"
                for name, st in self.agent_states.items()
                if st.get("state") == "working"
            ]
            _all_agent_states = [
                f"{name}={st.get('state', '?')}" for name, st in self.agent_states.items()
            ]
            _task_statuses = [f"{tid}={tstat}" for tid, tstat in self._dag_task_statuses.items()]
            _graph_info = "no graph"
            if self._current_dag_graph:
                _tasks = self._current_dag_graph.get("tasks", [])
                _graph_info = f"{len(_tasks)} tasks: {[t.get('id', '?') for t in _tasks]}"
            logger.error(
                f"[{self.project_id}] DAG crash state dump:\n"
                f"  Exception: {type(exc).__name__}: {str(exc)[:500]}\n"
                f"  Working agents at crash: {_working_agents or 'none'}\n"
                f"  All agent states: {_all_agent_states}\n"
                f"  Task statuses: {_task_statuses or 'none'}\n"
                f"  DAG graph: {_graph_info}\n"
                f"  Total cost: ${self.total_cost_usd:.4f}\n"
                f"  Turn count: {self.turn_count}\n"
                f"  Budget limit: ${self._effective_budget:.2f}"
            )
            await self._send_final(
                "\u274c **Execution failed.** An internal error occurred and the session could not complete.\n\n"
                "---\n\ud83d\udcac Send another message to retry or try a different approach."
            )
        finally:
            # _dag_exit_reason is set to "normal" at end of try, "cancelled" or "error: ..." in except blocks
            # Default "unknown" means we never reached the end of the try block (unexpected)

            # FIX(C-4): Retry within the SAME asyncio.Task instead of
            # spawning a new task via call_later.  This eliminates the race
            # condition between stop() and retry: stop() cancels THIS task
            # directly instead of racing a callback that creates a new one.
            if _should_retry:
                _extra_drained = self._drain_cancellations()
                logger.info(
                    f"[{self.project_id}] Spurious CancelledError — retrying DAG in same task "
                    f"(attempt {_anyio_retries}/{MAX_ANYIO_RETRIES}), "
                    f"extra_cancellations_drained={_extra_drained}"
                )
                # Backoff before retry.  If stop() fires during sleep,
                # catch it and abort the retry cleanly.
                try:
                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    self._drain_cancellations()
                    if self._stop_event.is_set():
                        logger.info(
                            f"[{self.project_id}] stop() fired during DAG retry backoff — aborting"
                        )
                        _should_retry = False
                # Re-enter _run_dag_session as a tail call within the same task.
                if _should_retry:
                    return await self._run_dag_session(
                        user_message,
                        _retry_count=_anyio_retries,
                        _cached_graph=_last_graph,
                    )

            self.is_running = False
            self.turn_count += 1

            # Determine if agents that are still 'working' actually completed
            # or were interrupted by an error/cancellation.
            _abnormal_exit = _dag_exit_reason != "normal"
            if _abnormal_exit:
                logger.warning(
                    f"[{self.project_id}] DAG exited abnormally ({_dag_exit_reason}). "
                    f"Marking still-working agents as error/cancelled."
                )

            # Emit agent_finished for ALL agents still in 'working' state
            # so the frontend never shows stale ACTIVE cards after task ends.
            for agent_name, agent_state in list(self.agent_states.items()):
                if agent_state.get("state") == "working":
                    # Find the task_id for this agent from dag_task_statuses
                    task_id = None
                    for tid, tstat in self._dag_task_statuses.items():
                        if tstat == "working":
                            # Match by checking if this task belongs to this agent
                            if self._current_dag_graph:
                                for t in self._current_dag_graph.get("tasks", []):
                                    if t.get("id") == tid and t.get("role") == agent_name:
                                        task_id = tid
                                        break
                            if task_id:
                                break

                    # BUG-16 FIX: If the DAG exited abnormally (exception or
                    # cancellation), agents still in 'working' did NOT complete
                    # successfully. Mark them as error/cancelled, not done.
                    if _abnormal_exit:
                        final_state = "error" if "error" in _dag_exit_reason else "cancelled"
                        final_task_status = "failed" if final_state == "error" else "cancelled"
                    else:
                        final_state = "done"
                        final_task_status = "completed"

                    self.agent_states[agent_name] = {
                        "state": final_state,
                        "task": agent_state.get("task", ""),
                        "cost": agent_state.get("cost", 0),
                        "turns": agent_state.get("turns", 0),
                        "duration": agent_state.get("duration", 0),
                    }
                    # Mark the task status in dag_task_statuses
                    if task_id:
                        self._dag_task_statuses[task_id] = final_task_status
                        # Emit dag_task_update so the Plan View updates the task status
                        await self._emit_event(
                            "dag_task_update",
                            task_id=task_id,
                            status=final_task_status,
                            agent=agent_name,
                            failure_reason=_dag_exit_reason if _abnormal_exit else "",
                        )
                    await self._emit_event(
                        "agent_finished",
                        agent=agent_name,
                        cost=agent_state.get("cost", 0),
                        turns=agent_state.get("turns", 0),
                        duration=agent_state.get("duration", 0),
                        is_error=_abnormal_exit,
                        task_id=task_id,
                        task_status=final_task_status,
                        failure_reason=_dag_exit_reason if _abnormal_exit else "",
                    )

            # Also emit dag_task_update for tasks that never started (still pending)
            # so the Plan View shows them as cancelled/skipped, not stuck in 'pending'
            if _abnormal_exit and self._current_dag_graph:
                for t in self._current_dag_graph.get("tasks", []):
                    tid = t.get("id")
                    if tid and tid not in self._dag_task_statuses:
                        # Task never started — mark as cancelled
                        self._dag_task_statuses[tid] = "cancelled"
                        await self._emit_event(
                            "dag_task_update",
                            task_id=tid,
                            status="cancelled",
                            agent=t.get("role", "unknown"),
                            failure_reason=f"DAG terminated before task started: {_dag_exit_reason}",
                        )

            # Emit execution_error event so the frontend can show an error banner
            if _abnormal_exit:
                await self._emit_event(
                    "execution_error",
                    error_type="dag_crash" if "error" in _dag_exit_reason else "cancelled",
                    error_message=_dag_exit_reason,
                    working_agents=[
                        n
                        for n, s in self.agent_states.items()
                        if s.get("state") in ("error", "cancelled")
                    ],
                    completed_tasks=sum(
                        1 for s in self._dag_task_statuses.values() if s == "completed"
                    ),
                    total_tasks=len(self._current_dag_graph.get("tasks", []))
                    if self._current_dag_graph
                    else 0,
                    total_cost=self.total_cost_usd,
                )

            # Emit agent_finished for the Orchestrator itself so the Trace
            # row shows duration/cost instead of perpetual "running..."
            _orch_final_status = "completed" if not _abnormal_exit else "error"
            self.agent_states["orchestrator"] = {
                "state": "done" if not _abnormal_exit else "error",
                "task": _orch_final_status,
                "cost": self.total_cost_usd,
            }
            await self._emit_event(
                "agent_finished",
                agent="orchestrator",
                cost=self.total_cost_usd,
                turns=self.turn_count,
                duration=0,  # frontend calculates from started_at
                is_error=_abnormal_exit,
            )
            # Checkpoint the final state (including dag_graph) BEFORE clearing
            # so the DB has the complete execution record for /live recovery.
            await self._checkpoint_state(status=_orch_final_status)

            # Keep dag_graph available for /live endpoint (browser refresh).
            # Only clear dag_task_statuses working states — keep completed/failed.
            # The graph itself stays until the next session starts.
            # self._current_dag_graph = None  # REMOVED: keep for /live recovery
            await self._emit_event("project_status", status="idle")

    async def _on_dag_task_start(self, task: TaskInput):
        """Callback: fired when DAG executor starts a task."""
        import time as _time

        task._started_at = _time.time()  # stamp for real duration calculation
        logger.info(
            f"[{self.project_id}] TASK START: {task.id} ({task.role.value}) "
            f"goal='{task.goal[:100]}' deps={task.depends_on or 'none'} "
            f"is_remediation={task.is_remediation}"
        )
        prefix = "🔧 " if task.is_remediation else ""
        required = ""
        if task.required_artifacts:
            art_names = [a.value for a in task.required_artifacts]
            required = f" | Artifacts: {', '.join(art_names)}"
        self.agent_states[task.role.value] = {
            "state": "working",
            "task": task.goal[:120],
            "last_activity_at": time.time(),
            "last_activity_type": "started",
        }
        self._dag_task_statuses[task.id] = "working"
        await self._emit_event("dag_task_update", task_id=task.id, status="working")
        # Clear any prior silence alert for this agent (it just started)
        self._silence_alerted.discard(task.role.value)
        # activity feed, network trace, elapsed time, and SDK calls correctly.
        await self._emit_event(
            "agent_started",
            agent=task.role.value,
            task=task.goal[:300],
            task_id=task.id,
            is_remediation=task.is_remediation,
        )
        await self._send_result(
            f"🔄 {prefix}**{task.role.value}** — `{task.id}`\n_{task.goal[:120]}..._{required}"
        )

    async def _on_dag_task_done(self, task: TaskInput, output: TaskOutput):
        """Callback: fired when DAG executor completes a task."""
        import time as _time

        is_ok = output.is_successful()
        real_duration = round(_time.time() - getattr(task, "_started_at", _time.time()), 1)
        logger.info(
            f"[{self.project_id}] TASK DONE: {task.id} ({task.role.value}) "
            f"status={output.status.value} confidence={output.confidence:.2f} "
            f"turns={output.turns_used} cost=${output.cost_usd:.4f} "
            f"duration={real_duration}s "
            f"artifacts={len(output.artifacts)} files, {len(output.structured_artifacts)} structured"
        )
        if not is_ok:
            logger.warning(
                f"[{self.project_id}] TASK FAILED: {task.id} "
                f"category={output.failure_category.value if output.failure_category else 'none'} "
                f"details={output.failure_details[:200] if output.failure_details else 'none'} "
                f"issues={output.issues[:3] if output.issues else 'none'} "
                f"blockers={output.blockers[:3] if output.blockers else 'none'}"
            )

        # Build failure reason (clear, human-readable)
        failure_reason = ""
        if not is_ok:
            parts = []
            if output.failure_category:
                parts.append(output.failure_category.value.replace("_", " ").title())
            if output.failure_details:
                parts.append(output.failure_details[:200])
            elif output.issues:
                parts.append(output.issues[0][:200])
            elif output.blockers:
                parts.append(f"Blocked: {output.blockers[0][:150]}")
            failure_reason = " | ".join(parts) if parts else "Unknown error"

        # Update internal state
        self.agent_states[task.role.value] = {
            "state": "done" if is_ok else "error",
            "task": task.goal[:120],
            "cost": output.cost_usd,
            "turns": output.turns_used,
            "duration": real_duration,
        }
        self._dag_task_statuses[task.id] = "completed" if is_ok else "failed"
        await self._emit_event(
            "dag_task_update", task_id=task.id, status="completed" if is_ok else "failed"
        )

        # Emit agent_finished for Trace + Agent cards
        await self._emit_event(
            "agent_finished",
            agent=task.role.value,
            cost=output.cost_usd,
            turns=output.turns_used,
            duration=real_duration,
            is_error=not is_ok,
            task_id=task.id,
            task_status=output.status.value,
            failure_reason=failure_reason,
        )

        # Build result message for Activity Log / Chat
        icon = "\u2705" if is_ok else "\u274c"
        prefix = "\ud83d\udd27 " if task.is_remediation else ""
        artifact_info = ""
        if output.structured_artifacts:
            art_names = [a.title for a in output.structured_artifacts[:3]]
            artifact_info = f"\nArtifacts: {', '.join(art_names)}"

        if is_ok:
            result_text = (
                f"{icon} {prefix}**{task.role.value}** completed `{task.id}` "
                f"({real_duration}s, ${output.cost_usd:.4f})\n"
                f"{output.summary[:250]}{artifact_info}"
            )
        else:
            result_text = (
                f"{icon} {prefix}**{task.role.value}** failed `{task.id}` "
                f"({real_duration}s, ${output.cost_usd:.4f})\n"
                f"**Reason:** {failure_reason}{artifact_info}"
            )

        await self._emit_event(
            "agent_result",
            agent=task.role.value,
            text=result_text,
            task_id=task.id,
        )
        await self._send_result(result_text)

        # Checkpoint on agent completion (non-blocking)
        self._checkpoint_async(status="running")

    async def _on_dag_remediation(
        self,
        failed_task: TaskInput,
        failed_output: TaskOutput,
        remediation_task: TaskInput,
    ):
        """Callback: fired when DAG executor creates a self-healing remediation task."""
        category = failed_output.failure_category
        cat_str = category.value if category else "unknown"
        await self._emit_event(
            "self_healing",
            failed_task=failed_task.id,
            failure_category=cat_str,
            remediation_task=remediation_task.id,
            remediation_role=remediation_task.role.value,
        )
        await self._notify(
            f"🔧 **Self-healing:** Task {failed_task.id} failed ({cat_str}). "
            f"Auto-created fix task {remediation_task.id} ({remediation_task.role.value})."
        )

    async def _on_dag_agent_stream(self, agent_role: str, text: str, task_id: str = ""):
        """Callback: fired when a DAG agent streams text — enables real-time UI updates."""
        # Throttle: only emit if text is meaningful (>20 chars)
        if len(text) < 20:
            return
        # Truncate to avoid flooding the WebSocket
        summary = text[:200].replace("\n", " ").strip()
        # Update agent_states so heartbeat knows the agent is alive
        if agent_role in self.agent_states:
            self.agent_states[agent_role]["last_stream_at"] = time.time()
            self.agent_states[agent_role]["last_activity_at"] = time.time()
            self.agent_states[agent_role]["last_activity_type"] = "stream"
            # Agent is active — clear silence alert
            self._silence_alerted.discard(agent_role)
        await self._emit_event(
            "agent_update",
            agent=agent_role,
            summary=summary,
            status="working",
            task_id=task_id,
        )

    async def _on_dag_agent_tool_use(
        self, agent_role: str, tool_name: str, description: str = "", task_id: str = ""
    ):
        """Callback: fired when a DAG agent uses a tool — shows in Activity Log."""
        # Update agent_states so the heartbeat shows real tool activity
        tool_display = description[:120] if description else tool_name
        if agent_role in self.agent_states:
            self.agent_states[agent_role]["current_tool"] = tool_display
            count = self.agent_states[agent_role].get("tool_count", 0) + 1
            self.agent_states[agent_role]["tool_count"] = count
            self.agent_states[agent_role]["last_activity_at"] = time.time()
            self.agent_states[agent_role]["last_activity_type"] = "tool_use"
            # Agent is active — clear silence alert
            self._silence_alerted.discard(agent_role)
        summary = f"Using tool: {tool_name}"
        if description:
            summary += f" \u2014 {description[:100]}"
        await self._emit_event(
            "tool_use",
            agent=agent_role,
            tool=tool_name,
            tool_name=tool_name,
            description=description[:100] if description else tool_name,
            summary=summary,
            task_id=task_id,
        )

    def _record_dag_output(self, output: TaskOutput):
        """Store a TaskOutput in the conversation log for persistence."""
        artifact_lines = []
        if output.structured_artifacts:
            for art in output.structured_artifacts:
                artifact_lines.append(f"  [{art.type.value}] {art.title}: {art.summary}")
        artifacts_str = "\n".join(artifact_lines) if artifact_lines else "none"

        content = (
            f"[{output.task_id}] {output.status.value.upper()}\n"
            f"{output.summary}\n"
            f"Files: {', '.join(output.artifacts[:10]) or 'none'}\n"
            f"Structured Artifacts:\n{artifacts_str}\n"
            f"Confidence: {output.confidence:.2f} | Cost: ${output.cost_usd:.4f}"
        )
        if output.failure_category:
            content += f"\nFailure: {output.failure_category.value}"
        if output.issues:
            content += f"\nIssues: {'; '.join(output.issues[:3])}"

        self.conversation_log.append(
            Message(
                agent_name=output.task_id,
                role="Agent",
                content=content,
                cost_usd=output.cost_usd,
            )
        )

    async def _load_manifest(self) -> str:
        """Load .hivemind/PROJECT_MANIFEST.md if it exists."""
        manifest_path = Path(self.project_dir) / ".hivemind" / "PROJECT_MANIFEST.md"

        def _read() -> str:
            if manifest_path.exists():
                try:
                    return manifest_path.read_text(encoding="utf-8")[:4000]
                except Exception as _exc:
                    logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)
            return ""

        return await asyncio.to_thread(_read)

    async def _load_memory_snapshot(self) -> str:
        """Load .hivemind/memory_snapshot.json if it exists (structured memory for PM)."""
        snapshot_path = Path(self.project_dir) / ".hivemind" / "memory_snapshot.json"

        def _read() -> str:
            if snapshot_path.exists():
                try:
                    content = snapshot_path.read_text(encoding="utf-8")
                    if len(content) <= 8000:
                        return content
                    # Truncate-safe: parse and re-serialize with size limit
                    import json as _json

                    data = _json.loads(content)
                    # Remove large fields first to fit
                    for key in ["file_map", "key_decisions", "known_issues"]:
                        result = _json.dumps(data)
                        if len(result) <= 8000:
                            break
                        if key in data and isinstance(data[key], dict | list):
                            if isinstance(data[key], dict):
                                items = list(data[key].items())[:20]
                                data[key] = dict(items)
                            else:
                                data[key] = data[key][:10]
                    return _json.dumps(data)[:8000]
                except Exception as _exc:
                    logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)
            return ""

        return await asyncio.to_thread(_read)

    def _list_workspace_files(self, max_files: int = 200) -> str:
        """Return a concise file tree of the project directory for the PM Agent."""
        root = Path(self.project_dir)
        if not root.exists():
            return ""
        lines: list[str] = []
        # Directories to skip
        skip_dirs = {
            ".git",
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            "dist",
            "build",
            ".pytest_cache",
            ".mypy_cache",
        }
        try:
            for path in sorted(root.rglob("*")):
                if any(part in skip_dirs for part in path.parts):
                    continue
                if path.is_file():
                    rel = path.relative_to(root)
                    lines.append(str(rel))
                    if len(lines) >= max_files:
                        lines.append(f"... (truncated at {max_files} files)")
                        break
        except Exception as _exc:
            logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)
        return "\n".join(lines)

    # --- Core orchestration loop (legacy) ---

    async def _run_orchestrator(self, user_message: str, *, _retry_count: int = 0):
        """Main orchestrator loop (legacy regex-delegate system).

        DEPRECATED: This path is superseded by _run_dag_session() which uses
        typed contracts (TaskGraph/TaskOutput) instead of regex-parsed
        <delegate> blocks. Set USE_DAG_EXECUTOR=true (the default) to use
        the new system. This legacy path remains as a fallback only.

        Uses a cumulative retry count (_retry_count) to bound retries on
        spurious anyio CancelledErrors.  Previous implementation used
        unbounded tail-call recursion which reset the counter each time.
        """
        if _retry_count == 0:
            logger.warning(
                f"[{self.project_id}] Using legacy orchestrator path. "
                f"Set USE_DAG_EXECUTOR=true for the typed-contract DAG system."
            )
        start_time = time.monotonic()
        self._last_user_message = user_message  # Track for state persistence

        # Log user message
        self.conversation_log.append(Message(agent_name="user", role="User", content=user_message))
        await self.session_mgr.add_message(self.project_id, "user", "User", user_message)

        # Build initial prompt with conversation history for context
        workspace = await asyncio.to_thread(self._get_workspace_context)

        # Pre-flight check: verify project directory exists
        if not Path(self.project_dir).exists():
            logger.error(
                f"[{self.project_id}] Project directory does not exist: {self.project_dir}"
            )
            await self._send_final(
                f"❌ Project directory not found: `{self.project_dir}`\n\n"
                f"Create the directory or update the project settings."
            )
            self.is_running = False
            await self._emit_event("project_status", status="idle")
            return

        # Include recent conversation history so the orchestrator has context
        # even without session resume
        recent_msgs = await self.session_mgr.get_recent_messages(self.project_id, count=10)
        history = ""
        if recent_msgs:
            history_lines = []
            for msg in recent_msgs:
                role = msg.get("agent_name", "unknown")
                content = msg.get("content", "")[:500]
                history_lines.append(f"[{role}]: {content}")
            history = "Recent conversation history:\n" + "\n".join(history_lines) + "\n\n"

        prompt = f"Project: {self.project_name}\nWorking directory: {self.project_dir}\n\n"
        if workspace:
            prompt += f"{workspace}\n\n"
        if history:
            prompt += history

        # Inject task complexity hint so the orchestrator sets the right expectations upfront
        complexity = self._estimate_task_complexity(user_message)

        # If the manifest already exists, this is a continuation — inject it and override complexity
        existing_manifest = await asyncio.to_thread(self._read_project_manifest)
        if existing_manifest:
            # Keep EPIC if already detected — manifest only sets a *minimum* of LARGE
            complexity = complexity if complexity == "EPIC" else "LARGE"
            prompt += (
                f"\n\n📋 EXISTING PROJECT MANIFEST FOUND (.hivemind/PROJECT_MANIFEST.md):\n"
                f"This is a CONTINUATION of previous work. Read the manifest carefully before delegating.\n\n"
                f"{existing_manifest}\n"
            )

        complexity_hints = {
            "SIMPLE": (
                "⚡ TASK COMPLEXITY: SIMPLE — Handle efficiently in 1-2 rounds. "
                "Fix → verify → TASK_COMPLETE."
            ),
            "MEDIUM": (
                "⚡ TASK COMPLEXITY: MEDIUM — Plan 3-5 rounds. "
                "Implement → review → test → TASK_COMPLETE."
            ),
            "LARGE": (
                "⚡ TASK COMPLEXITY: LARGE — Plan 6-10 rounds. "
                "Explore → implement phase by phase → review → test → TASK_COMPLETE. "
                "Do NOT rush to completion."
            ),
            "EPIC": (
                "⚡ TASK COMPLEXITY: EPIC — This is a large system build. Plan 10-25 rounds.\n"
                "You MUST work through ALL 6 phases:\n"
                "  Phase 1: Architecture + explore existing code (rounds 1-3)\n"
                "  Phase 2: Core foundation — models, DB, config (rounds 4-8)\n"
                "  Phase 3: Feature implementation — one feature at a time (rounds 9-13)\n"
                "  Phase 4: Integration + error handling (rounds 14-17)\n"
                "  Phase 5: Testing — write + run tests, fix failures (rounds 18-22)\n"
                "  Phase 6: Polish + deployment config (rounds 23+)\n"
                "TASK_COMPLETE only when: all features work + tests pass + app starts."
            ),
        }
        prompt += f"\n\n{complexity_hints.get(complexity, '')}\n\nUser request:\n{user_message}"

        # Initialize the task ledger (todo.md) — persistent file-system context
        orch_experience.init_todo(self, user_message, complexity)
        todo_content = orch_experience.read_todo(self)
        if todo_content:
            prompt += (
                f"\n\n📋 TASK LEDGER (.hivemind/todo.md):\n"
                f"This file tracks your progress. You can read it with your tools. "
                f"Update it by delegating developer to edit .hivemind/todo.md when phases complete.\n\n"
                f"{todo_content[:2000]}\n"
            )

        # ── Experience Memory Injection ──
        # Retrieve relevant lessons from past tasks and inject them into the prompt.
        # This gives the orchestrator "memory" of what worked and what failed before.
        try:
            experience_context = await orch_experience.inject_experience_context(self, user_message)
            if experience_context:
                prompt += experience_context
                logger.info(
                    f"[{self.project_id}] Injected experience context ({len(experience_context)} chars)"
                )
        except Exception as e:
            logger.debug(f"[{self.project_id}] Experience injection failed (non-fatal): {e}")

        task_history_id = None  # Guard: prevents NameError in except blocks
        _anyio_retries = _retry_count  # Cumulative across retries
        _should_retry = False
        _orch_exit_reason = "unknown"  # Track how the orchestrator loop exited

        # FIX(C-4): Retries happen via tail-call within the SAME asyncio.Task
        # (replaces call_later + new task pattern).  stop() can cancel THIS
        # task directly without racing a callback.

        # Drain residual cancellations for retry iterations
        if _retry_count > 0:
            _residual = self._drain_cancellations()
            logger.info(
                f"[{self.project_id}] Orchestrator retry #{_retry_count} starting "
                f"(residual_cancellations_drained={_residual})"
            )

        try:
            # Record task history
            task_history_id = await self.session_mgr.add_task_history(
                project_id=self.project_id,
                user_id=self.user_id,
                task_description=user_message[:500],
                status="running",
            )

            # Main loop: orchestrator responds, optionally delegates, then loops
            orchestrator_input = prompt
            loop_count = 0
            self._current_loop = 0
            max_loops = MAX_ORCHESTRATOR_LOOPS  # Safety limit on orchestrator iterations
            self._completed_rounds = []  # Track what has been done each round (instance-level for final summary)
            self._agents_used = set()  # Reset agent participation tracking for new session
            self.shared_context = []  # Reset shared context for new session (prevents leaking from previous task)
            self._budget_warning_sent = False  # Reset budget warning flag for new session

            while self.is_running and loop_count < max_loops:
                if self._stop_event.is_set():
                    break

                # Wait until un-paused — poll every second so stop_event is respected
                while not self._pause_event.is_set():
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(1.0)
                if self._stop_event.is_set():
                    break

                # Check session timeout (60 min default)
                elapsed = time.monotonic() - start_time
                if elapsed >= SESSION_TIMEOUT_SECONDS:
                    logger.warning(
                        f"[{self.project_id}] Session timeout after {elapsed:.0f}s "
                        f"(limit: {SESSION_TIMEOUT_SECONDS}s)"
                    )
                    await self._send_final(
                        await self._build_final_summary(
                            user_message,
                            start_time,
                            status=f"Stopped (session timeout after {int(elapsed // 60)}m)",
                        )
                    )
                    break

                # Drain ONE pending message from the queue per loop iteration
                # (Bug #2 fix: process messages independently, not merged).
                # Remaining messages stay queued and will be picked up on the
                # next iteration, giving each its own orchestrator turn.
                try:
                    target_name, injected_msg = self._message_queue.get_nowait()
                    remaining = self._message_queue.qsize()
                    orchestrator_input = f"[User message to {target_name}]:\n{injected_msg}"
                    logger.info(
                        f"[{self.project_id}] Drained queued message for "
                        f"'{target_name}' ({remaining} still queued) — "
                        f"lifecycle: enqueued → drained → processed"
                    )
                    await self._notify(
                        f"📨 Processing message for *{target_name}*"
                        + (f" ({remaining} more queued)" if remaining else "")
                    )
                except asyncio.QueueEmpty:
                    pass  # No queued messages — use existing orchestrator_input

                self.turn_count += 1
                loop_count += 1
                self._current_loop = loop_count

                # Periodic checkpoint every N rounds (non-blocking safety net)
                if loop_count % self._CHECKPOINT_INTERVAL_ROUNDS == 0:
                    self._checkpoint_async(status="running")

                # Emit loop progress event
                await self._emit_event(
                    "loop_progress",
                    loop=loop_count,
                    max_loops=max_loops,
                    turn=self.turn_count,
                    max_turns=MAX_TURNS_PER_CYCLE,
                    cost=self.total_cost_usd,
                    max_budget=MAX_BUDGET_USD,
                )

                await self._notify(
                    f"{AGENT_EMOJI.get('orchestrator', '🔄')} Turn {self.turn_count}/{MAX_TURNS_PER_CYCLE} — "
                    f"*orchestrator* is {'planning & delegating' if self.multi_agent else 'working'}..."
                )

                # Query orchestrator
                self.current_agent = "orchestrator"
                self.agent_states["orchestrator"] = {
                    "state": "working",
                    "task": "planning & delegating" if self.multi_agent else "working",
                    "last_activity_at": time.time(),
                    "last_activity_type": "started",
                }
                await self._emit_event(
                    "agent_started",
                    agent="orchestrator",
                    task="planning & delegating" if self.multi_agent else "working",
                )
                agent_start = time.monotonic()

                # Rate limiting: enforce minimum gap between orchestrator calls
                # to avoid overwhelming the API on fast loops (stuck detection may be slow)
                _last = self._last_orch_call_time
                _gap = time.monotonic() - _last
                if _gap < RATE_LIMIT_SECONDS and loop_count > 0:
                    await asyncio.sleep(RATE_LIMIT_SECONDS - _gap)
                self._last_orch_call_time = time.monotonic()

                # Orchestrator heartbeat — emit periodic updates while it's thinking
                # so the UI knows it's not stuck (it has no tools, so no tool_use events)
                async def _orch_heartbeat(_agent_start=agent_start):
                    _last_real_tool = ""
                    _last_tool_time = time.monotonic()
                    while True:
                        await asyncio.sleep(AGENT_RETRY_DELAY)
                        elapsed = int(time.monotonic() - _agent_start)
                        state_info = self.agent_states.get("orchestrator", {})
                        real_tool = state_info.get("current_tool", "")

                        # Check if on_tool_use updated the state with real activity
                        if real_tool and real_tool != _last_real_tool:
                            _last_real_tool = real_tool
                            _last_tool_time = time.monotonic()
                            status = f"{real_tool} ({elapsed}s)"
                        else:
                            stale_secs = int(time.monotonic() - _last_tool_time)
                            if _last_real_tool and stale_secs < 15:
                                status = f"{_last_real_tool} ({elapsed}s)"
                            elif elapsed < 8:
                                status = f"analyzing request... ({elapsed}s)"
                            else:
                                status = f"thinking... ({elapsed}s)"

                        # Use update to preserve accumulated fields (tool_count, etc.)
                        orch_state = self.agent_states.get("orchestrator", {})
                        orch_state.update(
                            {
                                "state": "working",
                                "task": status,
                                "current_tool": status,
                            }
                        )
                        self.agent_states["orchestrator"] = orch_state
                        await self._emit_event(
                            "agent_update",
                            agent="orchestrator",
                            text=f"\U0001f3af {status}",
                            summary=f"\U0001f3af Orchestrator: {status}",
                            timestamp=time.time(),
                        )

                orch_hb = asyncio.create_task(_orch_heartbeat())
                try:
                    response = await self._query_agent("orchestrator", orchestrator_input)
                except asyncio.CancelledError:
                    # The orchestrator query runs WITHOUT isolation, so the SDK's
                    # anyio cancel-scope bug can leak a CancelledError here.
                    # Absorb it: drain ALL cancellations so the event loop stays clean.
                    _drained = self._drain_cancellations()
                    if self._stop_event.is_set():
                        raise  # Real cancellation — propagate
                    logger.warning(
                        f"[{self.project_id}] Orchestrator query got spurious CancelledError "
                        f"(anyio leak) — drained {_drained} cancellations, treating as transient error"
                    )
                    response = SDKResponse(
                        text="Orchestrator query was interrupted (anyio cancel scope leak). Retrying.",
                        is_error=True,
                        error_message="CancelledError (anyio leak)",
                    )
                finally:
                    orch_hb.cancel()
                    try:
                        await orch_hb
                    except asyncio.CancelledError:
                        pass

                agent_duration = time.monotonic() - agent_start
                logger.info(
                    f"[{self.project_id}] Orchestrator response: "
                    f"len={len(response.text)}, cost=${response.cost_usd:.4f}, "
                    f"turns={response.num_turns}, error={response.is_error}, "
                    f"has_delegate={'<delegate>' in response.text}, "
                    f"has_complete={'TASK_COMPLETE' in response.text}, "
                    f"duration={agent_duration:.1f}s"
                )
                self._record_response("orchestrator", "Orchestrator", response)
                self.current_agent = None
                self.current_tool = None
                # Preserve accumulated fields when updating orchestrator state
                orch_done_state = self.agent_states.get("orchestrator", {})
                orch_done_state.update(
                    {
                        "state": "error" if response.is_error else "done",
                        "cost": response.cost_usd,
                        "turns": response.num_turns,
                        "duration": agent_duration,
                    }
                )
                self.agent_states["orchestrator"] = orch_done_state
                await self._emit_event(
                    "agent_finished",
                    agent="orchestrator",
                    cost=response.cost_usd,
                    turns=response.num_turns,
                    duration=round(agent_duration, 1),
                    is_error=response.is_error,
                )

                if response.is_error:
                    error_msg = response.error_message.lower()
                    # Provide actionable messages for common errors
                    if (
                        "api key" in error_msg
                        or "invalid api" in error_msg
                        or "authentication" in error_msg
                    ):
                        await self._send_result(
                            "🔑 *Authentication Error*\n\n"
                            "The Claude agent can't authenticate.\n"
                            "Make sure the Claude CLI is installed and logged in.\n"
                            "Run: claude login\n\n"
                            "Docs: https://docs.anthropic.com/claude-code/getting-started"
                        )
                    elif "exit code 71" in error_msg or "exit code: 71" in error_msg:
                        await self._send_result(
                            "🔒 *macOS Sandbox Restriction (Exit Code 71)*\n\n"
                            "macOS is blocking the agent from accessing files in this directory.\n"
                            "This commonly happens with ~/Downloads.\n\n"
                            "Fix: Move the project folder:\n"
                            "  mv ~/Downloads/my-project ~/my-project\n\n"
                            "Then restart the server from the new location."
                        )
                    else:
                        # Transient error — retry with exponential backoff (up to 3 times)
                        _orch_retries = self._orch_error_retries
                        if _orch_retries < 3:
                            self._orch_error_retries = _orch_retries + 1
                            wait_time = min(5 * (2**_orch_retries), 30)
                            await self._notify(
                                f"⚠️ Orchestrator error (attempt {_orch_retries + 1}/3): "
                                f"{response.error_message[:200]}\n"
                                f"Retrying in {wait_time}s..."
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            self._orch_error_retries = 0
                            await self._send_result(
                                f"⚠️ Orchestrator error after 3 retries: {response.error_message}\n\n"
                                f"Use /resume to retry or /stop to end."
                            )
                    await self._self_pause("orchestrator error")
                    continue

                # Show orchestrator response as intermediate
                display_text = orch_review.strip_delegate_blocks(response.text)
                if display_text.strip():
                    summary = display_text[:2000]
                    if len(display_text) > 2000:
                        summary += "\n... (truncated)"
                    await self._send_result(
                        f"🎯 *orchestrator* — Turn {self.turn_count}\n"
                        f"💰 ${response.cost_usd:.4f} (total: ${self.total_cost_usd:.4f})\n\n"
                        f"{summary}"
                    )

                # Check completion — validate TASK_COMPLETE is actually appropriate
                if "TASK_COMPLETE" in response.text:
                    premature_reason = self._check_premature_completion(loop_count, user_message)
                    if premature_reason:
                        logger.warning(
                            f"[{self.project_id}] TASK_COMPLETE rejected (premature): {premature_reason}"
                        )
                        await self._notify(
                            "⚠️ *orchestrator* tried to finish early — pushing to continue..."
                        )
                        # Still run any delegate blocks that accompanied the TASK_COMPLETE
                        early_delegations = orch_review.parse_delegations(response.text)
                        if early_delegations:
                            sub_results = await self._run_sub_agents(early_delegations)
                            review = await orch_review.build_review_prompt(
                                self, sub_results, self._completed_rounds
                            )
                            orchestrator_input = review
                        else:
                            # No delegates — inject rejection and force planning
                            current_changes = await orch_review.detect_file_changes(self)
                            orchestrator_input = (
                                f"⛔ TASK_COMPLETE REJECTED — the task is not fully complete yet.\n\n"
                                f"Reason: {premature_reason}\n\n"
                                f"Current file changes:\n{current_changes}\n\n"
                                f"Rounds completed so far:\n"
                                + (
                                    "\n".join(f"  • {r}" for r in self._completed_rounds)
                                    or "  • (none yet)"
                                )
                                + "\n\nYou MUST keep working. What specific work is still needed?\n"
                                "Delegate the next phase of work now using <delegate> blocks."
                            )
                        continue
                    # Completion validated — accept it
                    if task_history_id is not None:
                        await self.session_mgr.update_task_history(
                            task_history_id,
                            "completed",
                            cost_usd=self.total_cost_usd,
                            turns_used=self.turn_count,
                            summary=display_text[:500]
                            if display_text.strip()
                            else "Task completed",
                        )

                    # ── Reflection Step (Reflexion pattern) ──
                    # Generate lessons learned from this task execution
                    # and store them for future tasks.
                    try:
                        reflection = await orch_experience.generate_reflection(
                            self, task=user_message, outcome="success", start_time=start_time
                        )
                        if reflection:
                            await orch_experience.store_lessons(
                                self, task=user_message, reflection=reflection, outcome="success"
                            )
                            logger.info(
                                f"[{self.project_id}] Reflection stored after successful completion"
                            )
                    except Exception as e:
                        logger.warning(
                            f"[{self.project_id}] Reflection step failed (non-fatal): {e}"
                        )

                    # Clear persisted state (task completed successfully)
                    try:
                        if self.session_mgr:
                            await self.session_mgr.clear_orchestrator_state(self.project_id)
                    except Exception as _exc:
                        logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)

                    await self._send_final(
                        await self._build_final_summary(user_message, start_time)
                    )
                    break

                # Check budget (global + per-project)
                effective_budget = MAX_BUDGET_USD
                try:
                    project_budget = await self.session_mgr.get_project_budget(self.project_id)
                    if project_budget > 0:
                        effective_budget = min(effective_budget, project_budget)
                except Exception as _exc:
                    logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)
                self._effective_budget = effective_budget  # Store for sub-agent budget checks

                if self.total_cost_usd >= effective_budget:
                    await self._notify(
                        f"💰 Budget limit reached (${self.total_cost_usd:.4f} / ${effective_budget:.2f}).\n"
                        f"Use /resume to continue or /stop to end."
                    )
                    await self._self_pause("budget limit")
                    continue

                # Progressive budget warning at BUDGET_WARNING_THRESHOLD (default 80%)
                warning_threshold = effective_budget * BUDGET_WARNING_THRESHOLD
                if self.total_cost_usd >= warning_threshold and not self._budget_warning_sent:
                    self._budget_warning_sent = True
                    pct = int(self.total_cost_usd / effective_budget * 100)
                    await self._notify(
                        f"⚠️ Budget at {pct}% — ${self.total_cost_usd:.4f} of ${effective_budget:.2f} used.\n"
                        f"Will auto-pause at 100%. Use /stop to end early."
                    )

                # Check turn limit
                if self.turn_count >= MAX_TURNS_PER_CYCLE:
                    await self._notify(
                        f"⏰ Reached max turns ({MAX_TURNS_PER_CYCLE}).\n"
                        f"Use /resume to continue or /stop to end."
                    )
                    await self._self_pause("turn limit")
                    continue

                # Parse delegations
                delegations = orch_review.parse_delegations(response.text)
                logger.info(
                    f"[{self.project_id}] Parsed {len(delegations)} delegations: {[f'{d.agent}:{d.task[:40]}' for d in delegations]}"
                )

                # Mark delegated agents as queued (state=working) immediately so the
                # frontend never shows STANDBY between delegation and agent_started events.
                for d in delegations:
                    self.agent_states[d.agent] = {
                        "state": "working",
                        "task": d.task[:300],
                        "last_activity_at": time.time(),
                        "last_activity_type": "started",
                    }
                    # Clear any prior silence alert for this agent
                    self._silence_alerted.discard(d.agent)
                # Emit delegation events (frontend will confirm 'working' state)
                for d in delegations:
                    await self._emit_event(
                        "delegation",
                        from_agent="orchestrator",
                        to_agent=d.agent,
                        task=d.task[:300],
                    )

                # Emit orchestrator's plan summary to chat so users see what was decided
                if delegations:
                    plan_lines = [f"\ud83d\udcdd **Orchestrator Plan** (Round {loop_count}):"]
                    for i, d in enumerate(delegations, 1):
                        plan_lines.append(f"{i}. **{d.agent}**: {d.task[:120]}")
                    plan_summary = "\n".join(plan_lines)
                    await self._emit_event(
                        "agent_result",
                        agent="orchestrator",
                        text=plan_summary,
                    )
                    await self._send_result(plan_summary)

                if not delegations:
                    if self.multi_agent:
                        # No delegations in multi-agent mode — nudge to delegate or complete
                        logger.warning(
                            f"Orchestrator produced no parseable delegations. "
                            f"Response length: {len(response.text)}, "
                            f"contains '<delegate>': {'<delegate>' in response.text}"
                        )
                        # Build context-aware nudge — tell orchestrator what was done so far
                        rounds_so_far = (
                            "\n".join(f"  • {r}" for r in self._completed_rounds)
                            if self._completed_rounds
                            else "  • (no rounds completed yet — this is round 1)"
                        )
                        current_changes = await orch_review.detect_file_changes(self)
                        changes_line = (
                            f"\nCurrent file changes:\n{current_changes}"
                            if current_changes and "(no file" not in current_changes
                            else "\nNo file changes detected yet."
                        )
                        orchestrator_input = (
                            "⚠️ No <delegate> blocks found in your response.\n\n"
                            "You MUST either:\n"
                            "A) Delegate work using <delegate> blocks:\n"
                            "<delegate>\n"
                            '{"agent": "developer", "task": "specific task description", "context": "relevant file paths and details"}\n'
                            "</delegate>\n\n"
                            "B) Say TASK_COMPLETE if the task is 100% verified done.\n\n"
                            f"═══ PROGRESS SO FAR ═══\n"
                            f"{rounds_so_far}"
                            f"{changes_line}\n\n"
                            "═══ BEFORE DECIDING, CHECK ═══\n"
                            "1. Was code actually written/modified? (see file changes above)\n"
                            "2. Was it reviewed by the reviewer agent?\n"
                            "3. Were tests run and did they pass?\n"
                            "4. Are there any BLOCKED or NEEDS_FOLLOWUP items to address?\n\n"
                            f"Original user request:\n{user_message}"
                        )
                        continue
                    else:
                        # Solo mode — orchestrator handled it directly, done
                        await self._send_final(
                            await self._build_final_summary(user_message, start_time)
                        )
                        break

                if not self.multi_agent:
                    # Single-agent mode — ignore delegations
                    await self._send_final(
                        await self._build_final_summary(user_message, start_time)
                    )
                    break

                # Execute sub-agents
                logger.info(f"[{self.project_id}] Running {len(delegations)} sub-agent tasks...")
                # Mark orchestrator as "waiting" while sub-agents work
                agent_names = list({d.agent for d in delegations})
                self.agent_states["orchestrator"] = {
                    "state": "idle",
                    "task": f"waiting for {', '.join(agent_names)}",
                }
                await self._emit_event(
                    "agent_update",
                    agent="orchestrator",
                    text=f"waiting for {len(delegations)} sub-agent(s)",
                    summary=f"Orchestrator waiting for: {', '.join(agent_names)}",
                )
                # Execute sub-agents.  Each sub-agent query runs in an
                # isolated event loop (via isolated_query), so the anyio
                # cancel-scope bug is contained.  We still keep the retry
                # guard as a safety net for edge cases.
                try:
                    sub_results = await self._run_sub_agents(delegations)
                except asyncio.CancelledError:
                    if self._stop_event.is_set():
                        raise  # Real cancellation — propagate up
                    # Spurious anyio cancel-scope leak (should be rare now
                    # with event-loop isolation) — drain ALL cancellations and retry
                    _drained = self._drain_cancellations()
                    _anyio_retries += 1
                    logger.warning(
                        f"[{self.project_id}] Spurious CancelledError before/during sub-agents "
                        f"(retry {_anyio_retries}/{MAX_ANYIO_RETRIES}), drained={_drained}"
                    )
                    if _anyio_retries <= MAX_ANYIO_RETRIES:
                        await self._notify("⚠️ Internal hiccup — retrying automatically...")
                        continue  # Retry the while loop iteration
                    else:
                        raise  # Too many retries — propagate up
                logger.info(
                    f"[{self.project_id}] Sub-agents finished: "
                    f"{', '.join(f'{k}({len(v)} tasks)' for k, v in sub_results.items())}"
                )

                # Auto-commit safety net: ensure agents' work is saved
                # even if they forgot to commit (prevents work loss on crash)
                try:
                    commit_result = await asyncio.wait_for(
                        asyncio.create_subprocess_exec(
                            "git",
                            "-C",
                            self.project_dir,
                            "diff",
                            "--quiet",
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        ),
                        timeout=GIT_DIFF_TIMEOUT,
                    )
                    await commit_result.wait()
                    if commit_result.returncode != 0:
                        # There are uncommitted changes — auto-commit them
                        add_proc = await asyncio.create_subprocess_exec(
                            "git",
                            "-C",
                            self.project_dir,
                            "add",
                            "-A",
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await add_proc.wait()
                        agents_in_round = ", ".join(sub_results.keys())
                        commit_proc = await asyncio.create_subprocess_exec(
                            "git",
                            "-C",
                            self.project_dir,
                            "commit",
                            "-m",
                            f"auto: save work from round {loop_count} ({agents_in_round})",
                            "--no-verify",
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await commit_proc.wait()
                        if commit_proc.returncode == 0:
                            logger.info(
                                f"[{self.project_id}] Auto-committed uncommitted changes after round {loop_count}"
                            )
                except Exception as e:
                    logger.debug(
                        f"[{self.project_id}] Auto-commit check failed (non-critical): {e}"
                    )

                # Check stuck detection (enhanced with auto-escalation)
                stuck_info = self._detect_stuck()
                if stuck_info:
                    severity = stuck_info["severity"]
                    signal = stuck_info["signal"]
                    details = stuck_info["details"]
                    strategy = stuck_info["strategy"]

                    # Broadcast stuckness_detected event for dashboard alerts
                    await self._emit_stuckness_event(stuck_info)

                    if severity == "critical":
                        # Critical: pause and notify user
                        await self._notify(
                            f"🔁 **Stuck detected** ({signal})\n\n"
                            f"{details}\n\n"
                            f"Suggested strategy: **{strategy}**\n"
                            f"Use /talk orchestrator <message> to intervene, or /stop to end."
                        )
                        await self._self_pause(f"stuck detection: {signal}")
                        continue
                    else:
                        # Warning: inject escalation hint into the review prompt
                        # but don't pause — let the orchestrator try to self-correct
                        self._stuck_escalation_hint = (
                            f"⚠️ STUCK WARNING ({signal}): {details}\n"
                            f"Suggested strategy: {strategy}. "
                            f"You MUST change your approach this round — do NOT repeat the same delegations."
                        )
                        logger.warning(
                            f"[{self.project_id}] Stuck warning ({signal}): "
                            f"injecting escalation hint into review prompt"
                        )

                # ═══ EVALUATOR-REFLECT-REFINE LOOP ═══
                # Before sending results to the orchestrator, automatically run
                # verification (tests/build) if code was changed. If tests fail,
                # send the developer back to fix WITHOUT wasting an orchestrator turn.
                eval_result = await orch_review.auto_evaluate(self, sub_results, loop_count)
                if eval_result and eval_result.get("auto_fixed"):
                    # Developer was auto-retried and the fix results are in sub_results
                    sub_results = eval_result["updated_results"]
                    logger.info(
                        f"[{self.project_id}] Evaluator auto-fix applied in round {loop_count}"
                    )

                # Track what was done this round
                _round_summary = ", ".join(
                    f"{role}({'OK' if all(not r.is_error for r in resps) else 'ERR'})"
                    for role, resps in sub_results.items()
                )
                self._completed_rounds.append(f"Round {loop_count}: {_round_summary}")
                # Trim to last 15 rounds to prevent unbounded growth in review prompts
                if len(self._completed_rounds) > 15:
                    self._completed_rounds = self._completed_rounds[-15:]

                # Update the persistent task ledger with this round's results
                orch_experience.update_todo_after_round(self, loop_count, _round_summary)

                # Checkpoint on agent completion (non-blocking)
                self._checkpoint_async(status="running")

                # Inject evaluation results into the review prompt context
                eval_context = ""
                if eval_result:
                    eval_context = eval_result.get("summary", "")

                # Feed results back to orchestrator with round history
                orchestrator_input = await orch_review.build_review_prompt(
                    self, sub_results, self._completed_rounds
                )

                # Add evaluation results if available
                if eval_context:
                    orchestrator_input += f"\n\n═══ AUTO-EVALUATION RESULTS ═══\n{eval_context}\n"

                # Inject current task ledger into the review prompt so the
                # orchestrator always has the persistent goal + progress visible
                todo_content = await asyncio.to_thread(orch_experience.read_todo, self)
                if todo_content:
                    orchestrator_input += (
                        f"\n\n📋 TASK LEDGER (.hivemind/todo.md):\n{todo_content[:2000]}\n"
                    )

        except asyncio.CancelledError:
            # Distinguish real cancellation (user pressed Stop) from spurious
            # anyio cancel-scope leaks.  The SDK's anyio TaskGroup cleanup can
            # propagate CancelledError to the event loop when a generator is
            # GC'd in a different task.  If _stop_event is NOT set, this is a
            # spurious cancellation — we should NOT exit.
            if self._stop_event.is_set():
                logger.info(f"Orchestrator loop cancelled (stop requested) for {self.project_name}")
                if task_history_id is not None:
                    try:
                        await self.session_mgr.update_task_history(
                            task_history_id,
                            "cancelled",
                            cost_usd=self.total_cost_usd,
                            turns_used=self.turn_count,
                            summary="Task was cancelled",
                        )
                    except Exception as _exc:
                        logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)
                await self._send_final(
                    f"🛑 *{self.project_name}* — Task cancelled.\n"
                    f"📊 Turns: {self.turn_count} | 💰 ${self.total_cost_usd:.4f}"
                )
            else:
                # Spurious — drain ALL cancellations so we can keep running
                _drained = self._drain_cancellations()
                logger.warning(
                    f"Orchestrator loop got SPURIOUS CancelledError for {self.project_name} "
                    f"(stop_event not set — likely anyio cancel-scope leak). "
                    f"Drained {_drained} cancellations. "
                    f"Retrying the round ({_anyio_retries + 1}/{MAX_ANYIO_RETRIES})."
                )
                await self._notify("⚠️ Internal hiccup (anyio bug) — retrying automatically...")
                _anyio_retries += 1
                if _anyio_retries <= MAX_ANYIO_RETRIES:
                    _should_retry = True
                else:
                    await self._send_final(
                        f"⚠️ *{self.project_name}* — Repeated anyio errors ({_anyio_retries}x).\n"
                        f"Send your message again to retry.\n"
                        f"📊 Turns: {self.turn_count} | 💰 ${self.total_cost_usd:.4f}"
                    )
        except Exception as e:
            _orch_exit_reason = f"error: {type(e).__name__}: {str(e)[:200]}"
            # ── Comprehensive crash logging ──
            logger.error(f"Orchestrator loop error: {e}", exc_info=True)
            # Full state dump for post-mortem debugging
            _working_agents = [
                f"{name}(task={st.get('task', '?')[:60]}, cost=${st.get('cost', 0):.4f}, turns={st.get('turns', 0)})"
                for name, st in self.agent_states.items()
                if st.get("state") == "working"
            ]
            _all_agent_states = [
                f"{name}={st.get('state', '?')}" for name, st in self.agent_states.items()
            ]
            _task_statuses = [f"{tid}={tstat}" for tid, tstat in self._dag_task_statuses.items()]
            logger.error(
                f"[{self.project_id}] Orchestrator crash state dump:\n"
                f"  Exception: {type(e).__name__}: {str(e)[:500]}\n"
                f"  Loop count: {loop_count}/{max_loops}\n"
                f"  Working agents: {_working_agents or 'none'}\n"
                f"  All agent states: {_all_agent_states}\n"
                f"  Task statuses: {_task_statuses or 'none'}\n"
                f"  Total cost: ${self.total_cost_usd:.4f}\n"
                f"  Turn count: {self.turn_count}\n"
                f"  Completed rounds: {len(self._completed_rounds)}"
            )
            # Use _send_final (not _notify) so the frontend receives an agent_final event
            await self._send_final(
                f"❌ *{self.project_name}* — Error in orchestrator:\n{e}\n\n"
                f"📊 Turns: {self.turn_count} | 💰 ${self.total_cost_usd:.4f}\n"
                f"Send another message to retry."
            )
            if task_history_id is not None:
                try:
                    await self.session_mgr.update_task_history(
                        task_history_id,
                        "error",
                        cost_usd=self.total_cost_usd,
                        turns_used=self.turn_count,
                        summary=f"Error: {e}",
                    )
                except Exception as _exc:
                    logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)

            # ── Reflection on failed task ──
            try:
                reflection = await orch_experience.generate_reflection(
                    self,
                    task=user_message,
                    outcome=f"failure: {str(e)[:200]}",
                    start_time=start_time,
                )
                if reflection:
                    await orch_experience.store_lessons(
                        self, task=user_message, reflection=reflection, outcome="failure"
                    )
            except Exception as _exc:
                logger.debug(
                    "[Orchestrator] non-fatal exception suppressed: %s", _exc
                )  # Don't let reflection failure mask the original error
        else:
            _orch_exit_reason = "normal"
            # Loop exited normally (not via exception).
            # If we hit the safety limit without a clean exit, send a final summary.
            if loop_count >= max_loops:
                if task_history_id is not None:
                    try:
                        await self.session_mgr.update_task_history(
                            task_history_id,
                            "incomplete",  # task hit loop limit — not fully done
                            cost_usd=self.total_cost_usd,
                            turns_used=self.turn_count,
                            summary="Stopped (loop limit reached) — task may be incomplete",
                        )
                    except Exception as _exc:
                        logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)

                # ── Reflection on incomplete task ──
                try:
                    reflection = await orch_experience.generate_reflection(
                        self,
                        task=user_message,
                        outcome="partial (loop limit reached)",
                        start_time=start_time,
                    )
                    if reflection:
                        await orch_experience.store_lessons(
                            self, task=user_message, reflection=reflection, outcome="partial"
                        )
                        logger.info(f"[{self.project_id}] Reflection stored after loop-limit exit")
                except Exception as e:
                    logger.warning(
                        f"[{self.project_id}] Reflection on loop-limit failed (non-fatal): {e}"
                    )

                await self._send_final(
                    await self._build_final_summary(
                        user_message, start_time, status="Stopped (loop limit)"
                    )
                )
        finally:
            # FIX(C-4): Retry within the SAME asyncio.Task instead of
            # spawning a new task via call_later.  stop() can cancel THIS
            # task directly without racing a callback.
            if _should_retry:
                _extra_drained = self._drain_cancellations()
                logger.info(
                    f"[{self.project_id}] Orchestrator retry in same task "
                    f"(attempt {_anyio_retries}/{MAX_ANYIO_RETRIES}), "
                    f"extra_drained={_extra_drained}"
                )
                # Backoff before retry.  If stop() fires during sleep,
                # catch it and abort the retry cleanly.
                try:
                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    self._drain_cancellations()
                    if self._stop_event.is_set():
                        logger.info(
                            f"[{self.project_id}] stop() fired during orchestrator retry backoff — aborting"
                        )
                        _should_retry = False
                # Re-enter via tail call within the same task.
                if _should_retry:
                    return await self._run_orchestrator(user_message, _retry_count=_anyio_retries)

            # Save final enriched checkpoint to DB before clearing — so refresh
            # and future resume can show the last-known state for each agent.
            _abnormal_exit = _orch_exit_reason != "normal"
            if _abnormal_exit:
                logger.warning(
                    f"[{self.project_id}] Orchestrator exited abnormally ({_orch_exit_reason}). "
                    f"Marking still-working agents as error/cancelled."
                )
            try:
                _ckpt_status = "completed" if not _abnormal_exit else "error"
                if self.session_mgr:
                    await self._checkpoint_state(status=_ckpt_status)
            except Exception as _exc:
                logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)
            if not self.is_paused:
                self.is_running = False

            # Emit agent_finished for ALL agents still in 'working' state
            # so the frontend never shows stale ACTIVE cards after task ends.
            for agent_name, agent_state in list(self.agent_states.items()):
                if agent_state.get("state") == "working":
                    task_id = None
                    for tid, tstat in self._dag_task_statuses.items():
                        if tstat == "working":
                            if self._current_dag_graph:
                                for t in self._current_dag_graph.get("tasks", []):
                                    if t.get("id") == tid and t.get("role") == agent_name:
                                        task_id = tid
                                        break
                            if task_id:
                                break

                    # BUG-16 FIX: If the orchestrator exited abnormally,
                    # agents still in 'working' did NOT complete successfully.
                    if _abnormal_exit:
                        final_state = "error" if "error" in _orch_exit_reason else "cancelled"
                        final_task_status = "failed" if final_state == "error" else "cancelled"
                    else:
                        final_state = "done"
                        final_task_status = "completed"

                    self.agent_states[agent_name] = {
                        "state": final_state,
                        "task": agent_state.get("task", ""),
                        "cost": agent_state.get("cost", 0),
                        "turns": agent_state.get("turns", 0),
                        "duration": agent_state.get("duration", 0),
                    }
                    if task_id:
                        self._dag_task_statuses[task_id] = final_task_status
                    await self._emit_event(
                        "agent_finished",
                        agent=agent_name,
                        cost=agent_state.get("cost", 0),
                        turns=agent_state.get("turns", 0),
                        duration=agent_state.get("duration", 0),
                        is_error=_abnormal_exit,
                        task_id=task_id,
                        task_status=final_task_status,
                        failure_reason=_orch_exit_reason if _abnormal_exit else "",
                    )

            # Always emit project_status so frontend knows the state changed
            await self._emit_event("project_status", status="paused" if self.is_paused else "idle")
            # Reset all agent states to idle — clear task so page-refresh doesn't
            # show STANDBY with a stale task description from the previous round.
            for agent_name in list(self.agent_states.keys()):
                prev = self.agent_states.get(agent_name, {})
                self.agent_states[agent_name] = {
                    "state": "idle",
                    "current_tool": None,
                    # Preserve accumulated cost/turns for the stats display
                    "cost": prev.get("cost", 0),
                    "turns": prev.get("turns", 0),
                }
            # NOTE: _on_task_done callback handles auto-restart if queue has pending messages

    # ── Agent execution (delegated to orch_agents.py) ──
    async def _query_agent(
        self, agent_role: str, prompt: str, skill_names: list[str] | None = None
    ) -> SDKResponse:
        """Route a query to the appropriate agent. Delegated to orch_agents."""
        return await orch_agents.query_agent(self, agent_role, prompt, skill_names)

    def _record_response(self, agent_name: str, role: str, response: SDKResponse):
        """Record an agent response. Delegated to orch_agents."""
        orch_agents.record_response(self, agent_name, role, response)

    def _get_available_skills_summary(self) -> str:
        """Build skill summary. Delegated to orch_agents."""
        return orch_agents.get_available_skills_summary()

    async def _run_sub_agents(self, delegations: list[Delegation]) -> dict[str, list[SDKResponse]]:
        """Execute sub-agent tasks with smart scheduling. Delegated to orch_agents."""
        return await orch_agents.run_sub_agents(self, delegations)

    def _extract_touched_files(self, text: str) -> set[str]:
        """Extract file paths from agent output. Delegated to orch_agents."""
        return orch_agents.extract_touched_files(text)

    def _detect_file_conflicts(self, files_touched: dict[str, set[str]]) -> dict[str, list[str]]:
        """Detect files modified by multiple agents. Delegated to orch_agents."""
        return orch_agents.detect_file_conflicts(files_touched)
