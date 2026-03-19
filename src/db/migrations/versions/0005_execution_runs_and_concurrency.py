"""Migration 0005: Add execution_runs table, concurrency indexes, and memory versioning.

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-19

Context
-------
This migration supports multi-project concurrent execution and robust history:

1. CREATE TABLE execution_runs — tracks individual DAG task lifecycles within an
   execution session. Each row = one task's start → artifacts → completion.

2. ADD composite indexes for concurrent query patterns:
   - execution_sessions(project_id, status) — find running sessions per project
   - execution_runs(project_id, status) — find running tasks per project
   - execution_runs(session_id, status) — filter tasks within a session
   - execution_runs(session_id) — FK lookup

3. ADD memory.version column — optimistic locking counter for safe concurrent
   writes. Writers include version in WHERE and retry on zero-rows-affected.

All changes are backward compatible:
- New table and columns use safe defaults (version DEFAULT 1).
- Existing data is unaffected.
- Supports both SQLite (render_as_batch) and PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ---------------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------------
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Add execution_sessions + execution_runs tables, concurrency indexes, and memory.version."""

    # ── 0. Create execution_sessions table (parent of execution_runs) ────
    op.create_table(
        "execution_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("total_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "idx_exec_sessions_project_started",
        "execution_sessions",
        ["project_id", sa.text("started_at DESC")],
        if_not_exists=True,
    )

    # ── 1. Create execution_runs table ────────────────────────────────────
    op.create_table(
        "execution_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("execution_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(255), nullable=False),
        sa.Column("task_name", sa.String(500), nullable=True),
        sa.Column("role", sa.String(100), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("depends_on_json", sa.JSON(), nullable=True),
        sa.Column("artifacts_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── 2. Indexes on execution_runs ──────────────────────────────────────
    op.create_index(
        "idx_exec_runs_session_id",
        "execution_runs",
        ["session_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_exec_runs_project_status",
        "execution_runs",
        ["project_id", "status"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_exec_runs_session_status",
        "execution_runs",
        ["session_id", "status"],
        if_not_exists=True,
    )

    # ── 3. Composite index on execution_sessions for concurrent queries ──
    op.create_index(
        "idx_exec_sessions_project_status",
        "execution_sessions",
        ["project_id", "status"],
        if_not_exists=True,
    )

    # ── 4. Add memory.version for optimistic locking ─────────────────────
    # Use batch mode for SQLite compatibility (ALTER TABLE ADD COLUMN).
    with op.batch_alter_table("memory", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Remove execution_sessions, execution_runs tables, concurrency indexes, and memory.version."""

    # ── Remove memory.version ─────────────────────────────────────────────
    with op.batch_alter_table("memory", schema=None) as batch_op:
        batch_op.drop_column("version")

    # ── Remove concurrency index on execution_sessions ────────────────────
    op.drop_index(
        "idx_exec_sessions_project_status",
        table_name="execution_sessions",
        if_exists=True,
    )

    # ── Remove execution_runs indexes and table ───────────────────────────
    op.drop_index(
        "idx_exec_runs_session_status",
        table_name="execution_runs",
        if_exists=True,
    )
    op.drop_index(
        "idx_exec_runs_project_status",
        table_name="execution_runs",
        if_exists=True,
    )
    op.drop_index(
        "idx_exec_runs_session_id",
        table_name="execution_runs",
        if_exists=True,
    )
    op.drop_table("execution_runs")

    # ── Remove execution_sessions table ─────────────────────────────────
    op.drop_index(
        "idx_exec_sessions_project_started",
        table_name="execution_sessions",
        if_exists=True,
    )
    op.drop_table("execution_sessions")
