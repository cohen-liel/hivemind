"""Migration 0006: Add composite indexes for hot query patterns.

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-19

Context
-------
Adds composite indexes to accelerate the most frequent query patterns:

1. projects(user_id, updated_at) — dashboard lists user's projects sorted by activity.
2. agent_actions(task_id, agent_role, timestamp) — get_project_history groups actions
   by task_id and filters by agent_role with timestamp ordering.
3. agent_actions(conversation_id, action_type) — filtered aggregation queries
   in get_project_history_summary.
4. execution_runs(project_id, started_at) — chronological task listing per project.

All indexes use IF NOT EXISTS for idempotency. No data changes.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# ---------------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------------
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Add composite indexes for hot query patterns."""

    # ── 1. projects: user's projects sorted by last activity ──────────────
    op.create_index(
        "idx_projects_user_updated",
        "projects",
        ["user_id", "updated_at"],
        if_not_exists=True,
    )

    # ── 2. agent_actions: task-scoped agent history (task + role + time) ──
    op.create_index(
        "idx_agent_actions_task_role_ts",
        "agent_actions",
        ["task_id", "agent_role", "timestamp"],
        if_not_exists=True,
    )

    # ── 3. agent_actions: conversation + action_type aggregations ─────────
    op.create_index(
        "idx_agent_actions_conv_type",
        "agent_actions",
        ["conversation_id", "action_type"],
        if_not_exists=True,
    )

    # ── 4. execution_runs: chronological task listing per project ─────────
    op.create_index(
        "idx_exec_runs_project_started",
        "execution_runs",
        ["project_id", "started_at"],
        if_not_exists=True,
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Remove composite query indexes."""

    op.drop_index(
        "idx_exec_runs_project_started",
        table_name="execution_runs",
        if_exists=True,
    )
    op.drop_index(
        "idx_agent_actions_conv_type",
        table_name="agent_actions",
        if_exists=True,
    )
    op.drop_index(
        "idx_agent_actions_task_role_ts",
        table_name="agent_actions",
        if_exists=True,
    )
    op.drop_index(
        "idx_projects_user_updated",
        table_name="projects",
        if_exists=True,
    )
