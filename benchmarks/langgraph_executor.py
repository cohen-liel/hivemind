"""LangGraph-based DAG executor — drop-in replacement for dag_executor.py.

This uses LangGraph's StateGraph to orchestrate the same task DAG that
HiveMind's custom dag_executor.py handles. The goal is to compare:
  - Does LangGraph produce better code quality?
  - Is it faster/slower?
  - Does it use more/fewer tokens?

We keep the same isolated_query_openai tool executor (gpt-4.1-mini with
Read/Write/Edit/Bash/Glob/Grep) so the only variable is the orchestration
layer.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph

# Ensure hivemind root is on path
HIVEMIND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HIVEMIND_ROOT))
sys.path.insert(0, str(HIVEMIND_ROOT / "benchmarks"))

import isolated_query_openai

sys.modules["isolated_query"] = isolated_query_openai

from contracts import TaskGraph
from sdk_client import SDKResponse

logger = logging.getLogger(__name__)


# ── LangGraph State ────────────────────────────────────────────────────────


class DAGState(TypedDict):
    """State shared across all nodes in the LangGraph."""

    project_dir: str
    tasks: list[dict]  # Serialized TaskInput list
    completed_tasks: list[str]  # IDs of completed tasks
    task_results: dict[str, dict]  # task_id -> {status, text, tokens, time}
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    specialist_prompts: dict[str, str]
    current_round: int
    max_rounds: int


# ── Node Functions ─────────────────────────────────────────────────────────


def _get_ready_tasks(state: DAGState) -> list[dict]:
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


async def _execute_task(
    task: dict,
    state: DAGState,
) -> dict:
    """Execute a single task using isolated_query_openai."""
    task_id = task["id"]
    role = task["role"]
    goal = task["goal"]
    project_dir = state["project_dir"]

    # Build system prompt from specialist prompts
    system_prompt = state["specialist_prompts"].get(role, "")
    if not system_prompt:
        system_prompt = state["specialist_prompts"].get("backend_developer", "")

    # Add project context
    system_prompt += f"\n\nPROJECT BOUNDARY: {project_dir}\nWork ONLY within this directory."

    # Build prompt with context from dependencies
    prompt_parts = [goal]
    for dep_id in task.get("context_from", []):
        dep_result = state["task_results"].get(dep_id, {})
        if dep_result.get("text"):
            prompt_parts.append(
                f"\n\n<context from {dep_id}>\n{dep_result['text'][:2000]}\n</context>"
            )

    prompt = "\n".join(prompt_parts)

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

        return {
            "task_id": task_id,
            "status": "failed" if response.is_error else "completed",
            "text": response.text,
            "tokens": response.total_tokens,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "time": round(elapsed, 1),
            "turns": response.num_turns,
        }
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(f"Task {task_id} failed: {e}")
        return {
            "task_id": task_id,
            "status": "failed",
            "text": str(e),
            "tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "time": round(elapsed, 1),
            "turns": 0,
        }


def find_ready_tasks(state: DAGState) -> DAGState:
    """Router node: find which tasks are ready to execute."""
    state["current_round"] = state.get("current_round", 0) + 1
    return state


async def execute_ready_tasks(state: DAGState) -> DAGState:
    """Execute all ready tasks (sequentially for fair comparison)."""
    ready = _get_ready_tasks(state)

    if not ready:
        return state

    for task in ready:
        logger.info(f"[LangGraph] Executing task {task['id']} ({task['role']})")
        result = await _execute_task(task, state)

        state["completed_tasks"].append(task["id"])
        state["task_results"][task["id"]] = result
        state["total_tokens"] += result.get("tokens", 0)
        state["total_input_tokens"] += result.get("input_tokens", 0)
        state["total_output_tokens"] += result.get("output_tokens", 0)

        logger.info(
            f"[LangGraph] Task {task['id']}: {result['status']} "
            f"({result['turns']} turns, {result['tokens']} tokens, {result['time']}s)"
        )

    return state


def should_continue(state: DAGState) -> str:
    """Decide whether to continue executing or finish."""
    completed = set(state["completed_tasks"])
    all_task_ids = {t["id"] for t in state["tasks"]}

    if completed >= all_task_ids:
        return "done"

    if state.get("current_round", 0) >= state.get("max_rounds", 10):
        logger.warning("[LangGraph] Max rounds reached, stopping")
        return "done"

    ready = _get_ready_tasks(state)
    if not ready:
        logger.warning("[LangGraph] No ready tasks but not all complete — deadlock?")
        return "done"

    return "continue"


# ── Build the LangGraph ────────────────────────────────────────────────────


def build_dag_graph() -> StateGraph:
    """Build the LangGraph state graph for DAG execution."""
    workflow = StateGraph(DAGState)

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
class LangGraphResult:
    """Result from LangGraph DAG execution."""

    success_count: int = 0
    failure_count: int = 0
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    remediation_count: int = 0
    task_results: dict = field(default_factory=dict)
    elapsed_seconds: float = 0.0


async def execute_graph_langgraph(
    graph: TaskGraph,
    project_dir: str,
    specialist_prompts: dict[str, str],
) -> LangGraphResult:
    """Execute a TaskGraph using LangGraph instead of custom dag_executor."""

    # Serialize tasks for LangGraph state
    tasks_data = []
    for t in graph.tasks:
        tasks_data.append(
            {
                "id": t.id,
                "role": str(t.role),
                "goal": t.goal,
                "depends_on": t.depends_on or [],
                "context_from": getattr(t, "context_from", []) or [],
            }
        )

    initial_state: DAGState = {
        "project_dir": project_dir,
        "tasks": tasks_data,
        "completed_tasks": [],
        "task_results": {},
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "specialist_prompts": specialist_prompts,
        "current_round": 0,
        "max_rounds": 10,
    }

    dag = build_dag_graph()

    t0 = time.monotonic()
    final_state = await dag.ainvoke(initial_state)
    elapsed = time.monotonic() - t0

    # Count successes/failures
    successes = sum(
        1 for r in final_state["task_results"].values() if r.get("status") == "completed"
    )
    failures = len(final_state["task_results"]) - successes

    return LangGraphResult(
        success_count=successes,
        failure_count=failures,
        total_tokens=final_state["total_tokens"],
        total_input_tokens=final_state["total_input_tokens"],
        total_output_tokens=final_state["total_output_tokens"],
        task_results=final_state["task_results"],
        elapsed_seconds=round(elapsed, 1),
    )
