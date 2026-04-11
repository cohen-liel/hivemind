"""Enhanced LangGraph DAG executor — uses LangGraph for orchestration
but integrates all HiveMind features: Reflexion, Blackboard, Git, Artifacts.

This is a fair comparison: same features as the custom dag_executor,
but using LangGraph as the state machine backbone.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

# Ensure hivemind root is on path
HIVEMIND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HIVEMIND_ROOT))
sys.path.insert(0, str(HIVEMIND_ROOT / "benchmarks"))

import isolated_query_openai

sys.modules["isolated_query"] = isolated_query_openai

from blackboard import Blackboard
from contracts import AgentRole, TaskGraph, TaskInput, extract_task_output
from file_output_manager import ArtifactRegistry
from git_discipline import commit_single_task
from reflexion import run_reflexion, should_reflect
from sdk_client import SDKResponse
from structured_notes import NoteCategory, StructuredNotes

logger = logging.getLogger(__name__)


# ── LangGraph State ────────────────────────────────────────────────────────


class EnhancedDAGState(TypedDict):
    project_dir: str
    tasks: list[dict]
    completed_tasks: list[str]
    task_results: dict[str, dict]
    task_outputs: dict[str, Any]  # task_id -> TaskOutput for reflexion
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    specialist_prompts: dict[str, str]
    current_round: int
    max_rounds: int
    # HiveMind feature state
    artifact_registry: Any  # ArtifactRegistry instance
    structured_notes: Any  # StructuredNotes instance
    blackboard: Any  # Blackboard instance
    vision: str


# ── Helper Functions ───────────────────────────────────────────────────────


def _get_ready_tasks(state: EnhancedDAGState) -> list[dict]:
    """Find tasks whose dependencies are all completed."""
    completed = set(state["completed_tasks"])
    ready = []
    for task in state["tasks"]:
        if task["id"] in completed:
            continue
        deps = task.get("depends_on", [])
        if all(d in completed for d in deps):
            ready.append(task)
    return ready


def _task_dict_to_input(task_dict: dict) -> TaskInput:
    """Convert a task dict back to TaskInput for reflexion."""
    role_str = task_dict["role"]
    # Try to match to AgentRole enum
    try:
        role = AgentRole(role_str)
    except (ValueError, KeyError):
        role = AgentRole.BACKEND_DEVELOPER

    return TaskInput(
        id=task_dict["id"],
        role=role,
        goal=task_dict["goal"],
        depends_on=task_dict.get("depends_on", []),
        context_from=task_dict.get("context_from", []),
        acceptance_criteria=task_dict.get("acceptance_criteria", []),
    )


async def _execute_task_enhanced(
    task_dict: dict,
    state: EnhancedDAGState,
) -> dict:
    """Execute a single task with all HiveMind features."""
    task_id = task_dict["id"]
    role = task_dict["role"]
    goal = task_dict["goal"]
    project_dir = state["project_dir"]

    # ── Build system prompt ──
    system_prompt = state["specialist_prompts"].get(role, "")
    if not system_prompt:
        system_prompt = state["specialist_prompts"].get("backend_developer", "")
    system_prompt += f"\n\nPROJECT BOUNDARY: {project_dir}\nWork ONLY within this directory."

    # ── Build prompt with context from dependencies ──
    prompt_parts = [goal]

    # Inject artifact context from upstream tasks
    for dep_id in task_dict.get("context_from", []):
        dep_result = state["task_results"].get(dep_id, {})
        if dep_result.get("text"):
            prompt_parts.append(
                f"\n\n<context from {dep_id}>\n{dep_result['text'][:2000]}\n</context>"
            )

    # ── Inject Blackboard context ──
    try:
        blackboard = state["blackboard"]
        bb_ctx = blackboard.build_smart_context(
            current_role=role,
            task_goal=goal,
            max_tokens=500,
        )
        if bb_ctx:
            prompt_parts.append(f"\n\n<team_notes>\n{bb_ctx}\n</team_notes>")
            logger.info(
                f"[LangGraph+] Task {task_id}: injected Blackboard context ({len(bb_ctx)} chars)"
            )
    except Exception as e:
        logger.debug(f"[LangGraph+] Blackboard context failed (non-fatal): {e}")

    prompt = "\n".join(prompt_parts)

    # ── Execute task ──
    t0 = time.monotonic()
    try:
        response: SDKResponse = await isolated_query_openai.isolated_query(
            sdk=None,
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=project_dir,
            max_turns=30,
            max_budget_usd=5.0,
        )
        elapsed = time.monotonic() - t0

        # ── Extract structured output ──
        task_input = _task_dict_to_input(task_dict)
        task_output = extract_task_output(
            raw_text=response.text,
            task_id=task_id,
            task_role=role,
        )

        # ── Reflexion (self-critique) ──
        if should_reflect(task_input, task_output):
            logger.info(f"[LangGraph+] Task {task_id}: entering Reflexion phase")
            try:
                task_output, verdict = await run_reflexion(
                    task=task_input,
                    output=task_output,
                    session_id=None,
                    system_prompt=system_prompt,
                    project_dir=project_dir,
                    sdk=None,
                )
                logger.info(
                    f"[LangGraph+] Task {task_id}: Reflexion complete — "
                    f"{'needs_fix' if verdict.should_fix else 'pass'}"
                )
            except Exception as e:
                logger.warning(f"[LangGraph+] Reflexion failed (non-fatal): {e}")

        # ── Register artifacts ──
        try:
            n_registered = state["artifact_registry"].register(task_output)
            if n_registered:
                logger.info(f"[LangGraph+] Task {task_id}: {n_registered} artifacts registered")
        except Exception as e:
            logger.debug(f"[LangGraph+] Artifact registration failed: {e}")

        # ── Add structured notes ──
        try:
            notes = state["structured_notes"]
            notes.add(
                category=NoteCategory.CONTEXT,
                text=f"Task {task_id} completed by {role}",
                author_role=role,
                task_id=task_id,
            )
            if task_output.gotchas:
                for gotcha in task_output.gotchas[:3]:
                    notes.add(
                        category=NoteCategory.GOTCHA,
                        text=gotcha,
                        author_role=role,
                        task_id=task_id,
                    )
        except Exception as e:
            logger.debug(f"[LangGraph+] Notes failed: {e}")

        # ── Git commit ──
        try:
            await commit_single_task(
                project_dir=project_dir,
                task_id=task_id,
                outputs=[task_output],
            )
            logger.info(f"[LangGraph+] Task {task_id}: git committed")
        except Exception as e:
            logger.debug(f"[LangGraph+] Git commit failed: {e}")

        return {
            "task_id": task_id,
            "status": "failed" if response.is_error else "completed",
            "text": response.text,
            "tokens": response.total_tokens,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "time": round(elapsed, 1),
            "turns": response.num_turns,
            "task_output": task_output,
        }
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(f"[LangGraph+] Task {task_id} failed: {e}")
        return {
            "task_id": task_id,
            "status": "failed",
            "text": str(e),
            "tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "time": round(elapsed, 1),
            "turns": 0,
            "task_output": None,
        }


# ── LangGraph Nodes ────────────────────────────────────────────────────────


def find_ready_tasks(state: EnhancedDAGState) -> EnhancedDAGState:
    """Router node: increment round counter."""
    state["current_round"] = state.get("current_round", 0) + 1
    return state


async def execute_ready_tasks(state: EnhancedDAGState) -> EnhancedDAGState:
    """Execute all ready tasks with full HiveMind features."""
    ready = _get_ready_tasks(state)

    if not ready:
        return state

    for task in ready:
        logger.info(f"[LangGraph+] Executing task {task['id']} ({task['role']})")
        result = await _execute_task_enhanced(task, state)

        state["completed_tasks"].append(task["id"])
        state["task_results"][task["id"]] = result
        state["total_tokens"] += result.get("tokens", 0)
        state["total_input_tokens"] += result.get("input_tokens", 0)
        state["total_output_tokens"] += result.get("output_tokens", 0)

        if result.get("task_output"):
            state["task_outputs"][task["id"]] = result["task_output"]

        logger.info(
            f"[LangGraph+] Task {task['id']}: {result['status']} "
            f"({result['turns']} turns, {result['tokens']} tokens, {result['time']}s)"
        )

    return state


def should_continue(state: EnhancedDAGState) -> str:
    """Decide whether to continue executing or finish."""
    completed = set(state["completed_tasks"])
    all_task_ids = {t["id"] for t in state["tasks"]}

    if completed >= all_task_ids:
        return "done"

    if state.get("current_round", 0) >= state.get("max_rounds", 10):
        return "done"

    ready = _get_ready_tasks(state)
    if not ready:
        return "done"

    return "continue"


# ── Build the LangGraph ────────────────────────────────────────────────────


def build_enhanced_dag_graph() -> Any:
    """Build the LangGraph state graph with full HiveMind features."""
    workflow = StateGraph(EnhancedDAGState)

    workflow.add_node("find_ready", find_ready_tasks)
    workflow.add_node("execute", execute_ready_tasks)

    workflow.set_entry_point("find_ready")
    workflow.add_edge("find_ready", "execute")
    workflow.add_conditional_edges(
        "execute",
        should_continue,
        {
            "continue": "find_ready",
            "done": END,
        },
    )

    return workflow.compile()


# ── Public API ─────────────────────────────────────────────────────────────


@dataclass
class LangGraphEnhancedResult:
    """Result from enhanced LangGraph DAG execution."""

    success_count: int = 0
    failure_count: int = 0
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    remediation_count: int = 0
    task_results: dict = field(default_factory=dict)
    elapsed_seconds: float = 0.0


async def execute_graph_langgraph_enhanced(
    graph: TaskGraph,
    project_dir: str,
    specialist_prompts: dict[str, str],
) -> LangGraphEnhancedResult:
    """Execute a TaskGraph using LangGraph + all HiveMind features."""

    # Initialize HiveMind feature modules
    artifact_registry = ArtifactRegistry(project_dir)
    structured_notes = StructuredNotes(project_dir)
    structured_notes.init_session(graph.vision)
    blackboard = Blackboard(structured_notes)

    # Serialize tasks
    tasks_data = []
    for t in graph.tasks:
        tasks_data.append(
            {
                "id": t.id,
                "role": str(t.role),
                "goal": t.goal,
                "depends_on": t.depends_on or [],
                "context_from": getattr(t, "context_from", []) or [],
                "acceptance_criteria": getattr(t, "acceptance_criteria", []) or [],
            }
        )

    initial_state: EnhancedDAGState = {
        "project_dir": project_dir,
        "tasks": tasks_data,
        "completed_tasks": [],
        "task_results": {},
        "task_outputs": {},
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "specialist_prompts": specialist_prompts,
        "current_round": 0,
        "max_rounds": 10,
        "artifact_registry": artifact_registry,
        "blackboard": blackboard,
        "structured_notes": structured_notes,
        "vision": graph.vision,
    }

    dag = build_enhanced_dag_graph()

    t0 = time.monotonic()
    final_state = await dag.ainvoke(initial_state)
    elapsed = time.monotonic() - t0

    # Save artifact manifest
    try:
        artifact_registry.save_manifest()
    except Exception:
        pass

    # Count successes/failures
    successes = sum(
        1 for r in final_state["task_results"].values() if r.get("status") == "completed"
    )
    failures = len(final_state["task_results"]) - successes

    return LangGraphEnhancedResult(
        success_count=successes,
        failure_count=failures,
        total_tokens=final_state["total_tokens"],
        total_input_tokens=final_state["total_input_tokens"],
        total_output_tokens=final_state["total_output_tokens"],
        task_results=final_state["task_results"],
        elapsed_seconds=round(elapsed, 1),
    )
