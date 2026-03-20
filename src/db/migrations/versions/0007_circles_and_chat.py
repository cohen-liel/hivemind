"""Migration 0007: Add Circles and Chat tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-20

Context
-------
Adds six new tables for the Circles (group collaboration) and Chat
(real-time messaging) features:

1. circles              — Group/workspace entity.
2. circle_members       — Membership with roles (owner/admin/member/viewer).
3. circle_invitations   — Invitation system with tokens and expiry.
4. chat_channels        — Channels scoped to circles, projects, or DMs.
5. chat_messages        — Messages with threading support (self-ref FK).
6. message_read_receipts — Per-user, per-message read tracking.

Also adds a nullable circle_id FK to the existing projects table.

All operations use IF NOT EXISTS / IF EXISTS for idempotency.
Supports both SQLite and PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ---------------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------------
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_sqlite() -> bool:
    """Check if the current connection is SQLite."""
    return op.get_bind().dialect.name == "sqlite"


def _column_exists(table: str, column: str) -> bool:
    """Check if a column already exists in a table (idempotency)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def _table_exists(table: str) -> bool:
    """Check if a table already exists (idempotency)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    """Create Circles and Chat tables, add circle_id FK to projects."""

    # ── 1. circles ─────────────────────────────────────────────────────────
    if not _table_exists("circles"):
        op.create_table(
            "circles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("avatar_url", sa.String(1024), nullable=True),
            sa.Column("settings_json", sa.JSON(), nullable=True),
            sa.Column(
                "created_by",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("idx_circles_created_by", "circles", ["created_by"], if_not_exists=True)
        op.create_index("idx_circles_name", "circles", ["name"], if_not_exists=True)

    # ── 2. circle_members ──────────────────────────────────────────────────
    if not _table_exists("circle_members"):
        op.create_table(
            "circle_members",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "circle_id",
                sa.String(36),
                sa.ForeignKey("circles.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(50), nullable=False, server_default="member"),
            sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("circle_id", "user_id", name="uq_circle_member"),
        )
        op.create_index("idx_circle_members_circle_id", "circle_members", ["circle_id"], if_not_exists=True)
        op.create_index("idx_circle_members_user_id", "circle_members", ["user_id"], if_not_exists=True)
        op.create_index("idx_circle_members_circle_role", "circle_members", ["circle_id", "role"], if_not_exists=True)

    # ── 3. circle_invitations ──────────────────────────────────────────────
    if not _table_exists("circle_invitations"):
        op.create_table(
            "circle_invitations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "circle_id",
                sa.String(36),
                sa.ForeignKey("circles.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "invited_user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "invited_by_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("role", sa.String(50), nullable=False, server_default="member"),
            sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
            sa.Column("invite_token", sa.String(255), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("idx_circle_invitations_circle_id", "circle_invitations", ["circle_id"], if_not_exists=True)
        op.create_index("idx_circle_invitations_user_id", "circle_invitations", ["invited_user_id"], if_not_exists=True)
        op.create_index("idx_circle_invitations_circle_status", "circle_invitations", ["circle_id", "status"], if_not_exists=True)
        op.create_index("idx_circle_invitations_token", "circle_invitations", ["invite_token"], if_not_exists=True)

    # ── 4. Add circle_id FK to projects ────────────────────────────────────
    if not _column_exists("projects", "circle_id"):
        op.add_column(
            "projects",
            sa.Column(
                "circle_id",
                sa.String(36),
                nullable=True,
            ),
        )
        # SQLite doesn't support ADD CONSTRAINT for FKs after table creation,
        # but the column is still usable. FK enforcement happens at ORM level.
        if not _is_sqlite():
            op.create_foreign_key(
                "fk_projects_circle_id",
                "projects",
                "circles",
                ["circle_id"],
                ["id"],
                ondelete="SET NULL",
            )
        op.create_index(
            "idx_projects_circle_id",
            "projects",
            ["circle_id"],
            if_not_exists=True,
        )

    # ── 5. chat_channels ───────────────────────────────────────────────────
    if not _table_exists("chat_channels"):
        op.create_table(
            "chat_channels",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "circle_id",
                sa.String(36),
                sa.ForeignKey("circles.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "project_id",
                sa.String(36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("channel_type", sa.String(50), nullable=False, server_default="circle"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column(
                "created_by",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("idx_chat_channels_circle_id", "chat_channels", ["circle_id"], if_not_exists=True)
        op.create_index("idx_chat_channels_project_id", "chat_channels", ["project_id"], if_not_exists=True)
        op.create_index("idx_chat_channels_type", "chat_channels", ["channel_type"], if_not_exists=True)
        op.create_index("idx_chat_channels_circle_type", "chat_channels", ["circle_id", "channel_type"], if_not_exists=True)

    # ── 6. chat_messages ───────────────────────────────────────────────────
    if not _table_exists("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "channel_id",
                sa.String(36),
                sa.ForeignKey("chat_channels.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "sender_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "parent_message_id",
                sa.String(36),
                sa.ForeignKey("chat_messages.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("message_type", sa.String(50), nullable=False, server_default="text"),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("is_edited", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("idx_chat_messages_channel_created", "chat_messages", ["channel_id", "created_at"], if_not_exists=True)
        op.create_index("idx_chat_messages_sender_id", "chat_messages", ["sender_id"], if_not_exists=True)
        op.create_index("idx_chat_messages_parent_id", "chat_messages", ["parent_message_id"], if_not_exists=True)

    # ── 7. message_read_receipts ───────────────────────────────────────────
    if not _table_exists("message_read_receipts"):
        op.create_table(
            "message_read_receipts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "message_id",
                sa.String(36),
                sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("message_id", "user_id", name="uq_read_receipt_message_user"),
        )
        op.create_index("idx_read_receipts_message_id", "message_read_receipts", ["message_id"], if_not_exists=True)
        op.create_index("idx_read_receipts_user_id", "message_read_receipts", ["user_id"], if_not_exists=True)


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    """Remove Circles and Chat tables, drop circle_id from projects."""

    # Drop in reverse dependency order
    op.drop_table("message_read_receipts") if _table_exists("message_read_receipts") else None
    op.drop_table("chat_messages") if _table_exists("chat_messages") else None
    op.drop_table("chat_channels") if _table_exists("chat_channels") else None

    # Remove circle_id from projects
    if _column_exists("projects", "circle_id"):
        op.drop_index("idx_projects_circle_id", table_name="projects", if_exists=True)
        if not _is_sqlite():
            op.drop_constraint("fk_projects_circle_id", "projects", type_="foreignkey")
        op.drop_column("projects", "circle_id")

    op.drop_table("circle_invitations") if _table_exists("circle_invitations") else None
    op.drop_table("circle_members") if _table_exists("circle_members") else None
    op.drop_table("circles") if _table_exists("circles") else None
