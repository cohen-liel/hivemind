"""Incremental plan event builders for the persistent plan system.

This module provides builder functions for the new WebSocket event types
introduced by the persistent/incremental plan feature:

- ``plan_delta``: Sends only added/skipped tasks (not the full graph),
  enabling efficient incremental plan updates without retransmitting the
  entire task graph.

- Enhanced ``task_graph``: Optionally includes cumulative state (task_history,
  skipped_task_ids) so reconnecting clients can reconstruct the full plan.

- Enhanced ``dag_task_update``: Now supports ``skipped`` status alongside
  the existing ``working``, ``completed``, ``failed``, and ``cancelled``.

All events are backward-compatible — existing consumers that don't understand
the new fields will ignore them gracefully.

Usage::

    from src.api.events import (
        build_plan_delta_event,
        build_task_graph_event,
        build_dag_task_update_event,
    )

    # When the PM appends tasks to a running DAG:
    event = build_plan_delta_event(
        project_id="abc123",
        add_tasks=[new_task.model_dump() for new_task in new_tasks],
        skip_task_ids=["task_007", "task_008"],
    )
    await event_bus.publish(event)

    # When emitting the full graph with cumulative state:
    event = build_task_graph_event(
        project_id="abc123",
        graph=task_graph,
        cumulative=True,
    )
    await event_bus.publish(event)

    # When a task is skipped:
    event = build_dag_task_update_event(
        project_id="abc123",
        task_id="task_007",
        status="skipped",
        task_name="Implement caching layer",
        reason="Superseded by task_012",
    )
    await event_bus.publish(event)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contracts import TaskGraph


def build_plan_delta_event(
    project_id: str,
    add_tasks: list[dict[str, Any]] | None = None,
    skip_task_ids: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Build a ``plan_delta`` event carrying only the incremental changes.

    This event is emitted when the PM agent merges new tasks into a running
    DAG or marks tasks as skipped. Clients apply the delta to their local
    plan state rather than replacing the entire graph.

    The event is persisted by the EventBus so reconnecting clients can
    replay all deltas to reconstruct the full cumulative plan.

    Args:
        project_id:    UUID of the project.
        add_tasks:     List of serialised TaskInput dicts to append.
                       Each dict must contain at least ``id``, ``goal``,
                       ``role``, and ``depends_on``.
        skip_task_ids: List of task IDs to mark as SKIPPED.
        reason:        Human-readable reason for the skip (optional).

    Returns:
        Dict ready for ``event_bus.publish()`` / ``ws.send_json()``.

    Example::

        {
          "type": "plan_delta",
          "timestamp": 1741695600.0,
          "project_id": "abc123",
          "add_tasks": [
            {"id": "task_012", "goal": "Add Redis cache", "role": "backend_developer", "depends_on": ["task_003"]}
          ],
          "skip_task_ids": ["task_007"],
          "reason": "Superseded by new caching approach"
        }
    """
    return {
        "type": "plan_delta",
        "timestamp": time.time(),
        "project_id": project_id,
        "add_tasks": add_tasks or [],
        "skip_task_ids": skip_task_ids or [],
        "reason": reason,
    }


def build_task_graph_event(
    project_id: str,
    graph: TaskGraph,
    cumulative: bool = False,
) -> dict[str, Any]:
    """Build a ``task_graph`` event, optionally with cumulative plan state.

    When ``cumulative=True``, the event includes ``task_history`` and
    ``skipped_task_ids`` so reconnecting clients can reconstruct the
    full plan state from a single event (no delta replay needed).

    When ``cumulative=False`` (default), the event matches the existing
    ``task_graph`` schema for backward compatibility.

    Args:
        project_id:  UUID of the project.
        graph:       The TaskGraph model instance.
        cumulative:  If True, include task_history and skipped_task_ids.

    Returns:
        Dict ready for ``event_bus.publish()`` / ``ws.send_json()``.
    """
    payload: dict[str, Any] = {
        "type": "task_graph",
        "timestamp": time.time(),
        "project_id": project_id,
        "graph": graph.model_dump(),
    }

    if cumulative:
        payload["cumulative"] = True
        payload["task_history"] = graph.task_history
        payload["skipped_task_ids"] = list(graph._skipped_task_ids())

    return payload


def build_dag_task_update_event(
    project_id: str,
    task_id: str,
    status: str,
    task_name: str = "",
    agent: str = "",
    failure_reason: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Build a ``dag_task_update`` event with support for ``skipped`` status.

    Extends the existing dag_task_update contract with:
    - ``skipped`` as a valid status value
    - ``reason`` field explaining why a task was skipped

    All new fields are additive — existing consumers that only check for
    ``working``/``completed``/``failed``/``cancelled`` will simply ignore
    the ``skipped`` status and ``reason`` field.

    Args:
        project_id:     UUID of the project.
        task_id:        ID of the task whose status changed.
        status:         One of: ``pending``, ``working``, ``completed``,
                        ``failed``, ``cancelled``, ``skipped``.
        task_name:      Human-readable goal/name of the task.
        agent:          Agent role handling this task (if applicable).
        failure_reason: Reason for failure (only for ``failed`` status).
        reason:         Reason for skip (only for ``skipped`` status).

    Returns:
        Dict ready for ``event_bus.publish()`` / ``ws.send_json()``.
    """
    event: dict[str, Any] = {
        "type": "dag_task_update",
        "timestamp": time.time(),
        "project_id": project_id,
        "task_id": task_id,
        "status": status,
    }

    if task_name:
        event["task_name"] = task_name
    if agent:
        event["agent"] = agent
    if failure_reason:
        event["failure_reason"] = failure_reason
    if reason:
        event["reason"] = reason

    return event


def build_plan_snapshot_event(
    project_id: str,
    graph: TaskGraph,
    completed_task_ids: list[str] | None = None,
    failed_task_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a full plan snapshot for reconnecting clients.

    Emits a ``task_graph`` event with ``cumulative=True`` plus explicit
    lists of completed, failed, and skipped task IDs so the client can
    reconstruct the full visual state in one shot.

    This should be sent when a client reconnects (via the ring buffer
    catch-up or on explicit request) so it doesn't need to replay all
    individual ``plan_delta`` and ``dag_task_update`` events.

    Args:
        project_id:         UUID of the project.
        graph:              The current TaskGraph model instance.
        completed_task_ids: List of task IDs that are completed.
        failed_task_ids:    List of task IDs that have failed.

    Returns:
        Dict ready for ``event_bus.publish()`` / ``ws.send_json()``.
    """
    return {
        "type": "task_graph",
        "timestamp": time.time(),
        "project_id": project_id,
        "graph": graph.model_dump(),
        "cumulative": True,
        "task_history": graph.task_history,
        "skipped_task_ids": list(graph._skipped_task_ids()),
        "completed_task_ids": completed_task_ids or [],
        "failed_task_ids": failed_task_ids or [],
    }
