"""SQLAlchemy async ORM models for the multi-project agent platform.

This module defines the six core tables that serve as the Single Source of Truth
(SSOT) for all users, sessions, conversations, messages, agent actions, and project
memory. All state is persisted here — nothing is ephemeral-only.

Design decisions:
- UUIDs as primary keys (String(36)) — portable across SQLite and Postgres.
- JSON columns for flexible payloads (config, metadata, action payloads/results).
- Cascade DELETE rules enforce referential integrity at the DB level.
- Indexes cover the highest-frequency read patterns (hot-query paths).
- timezone=True on all DateTime columns — always store UTC.

Table hierarchy (FK ownership):
    users
    └── projects (user_id → users.id, nullable for legacy/anonymous)
        ├── conversations (project_id → projects.id)
        │   ├── messages (conversation_id → conversations.id)
        │   └── agent_actions (conversation_id → conversations.id)
        └── memory (project_id → projects.id)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from _shared_utils import utcnow as _utcnow


def _new_uuid() -> str:
    """Generate a new UUID4 string suitable for use as a primary key."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    pass


# ---------------------------------------------------------------------------
# Model: users
# ---------------------------------------------------------------------------


class User(Base):
    """Represents a platform user — the root of the multi-tenant hierarchy.

    Every user owns zero or more projects. Per-user DB isolation is supported
    via the ``db_path`` column: when non-null, that user's project data is
    stored in a dedicated SQLite file rather than the shared ``platform.db``.
    This enables true filesystem-level isolation without a Postgres schema-per-user
    setup, while remaining compatible with the single-DB default.

    Columns:
        id          - UUID primary key.
        external_id - Opaque string from the identity provider (OAuth sub, GitHub ID, etc.).
                      Null for anonymous or API-key-only users.
        email       - Unique email address. Null for anonymous users.
        display_name- Optional human-readable display name (e.g. "Alice Smith").
        db_path     - Absolute path to per-user SQLite database file.
                      Null → user's data lives in the shared platform.db.
                      Non-null → route all project queries to this file.
        created_at  - UTC timestamp of account creation.
        updated_at  - UTC timestamp of last profile modification.

    Relationships:
        projects - All projects owned by this user (cascade delete).

    Indexes:
        uq_users_external_id - UNIQUE on external_id (when non-null).
        uq_users_email        - UNIQUE on email (when non-null).
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    external_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        doc=(
            "Opaque identifier from an OAuth/identity provider (e.g. GitHub sub claim). "
            "Null for anonymous or local-only users."
        ),
    )
    email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        doc="Unique email address. Null for anonymous users.",
    )
    display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Human-readable display name for the user.",
    )
    db_path: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        doc=(
            "Absolute path to the per-user SQLite database file. "
            "Null → use the shared platform.db. "
            "Non-null → route all project/conversation queries to this file for isolation."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when the user account was created.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        doc="UTC timestamp of the last modification to the user record.",
    )

    # ── Relationships ──────────────────────────────────────────────────────
    projects: Mapped[list[Project]] = relationship(
        "Project",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )
    circle_memberships: Mapped[list[CircleMember]] = relationship(
        "CircleMember",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )
    circle_invitations: Mapped[list[CircleInvitation]] = relationship(
        "CircleInvitation",
        back_populates="invited_user",
        cascade="all, delete-orphan",
        lazy="select",
        foreign_keys="CircleInvitation.invited_user_id",
    )
    chat_messages: Mapped[list[ChatMessage]] = relationship(
        "ChatMessage",
        back_populates="sender",
        cascade="all, delete-orphan",
        lazy="select",
    )
    message_read_receipts: Mapped[list[MessageReadReceipt]] = relationship(
        "MessageReadReceipt",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id!r} email={self.email!r}>"


# ---------------------------------------------------------------------------
# Model: projects
# ---------------------------------------------------------------------------


class Project(Base):
    """Represents a top-level project that groups conversations and memory.

    A project is the highest-level isolation boundary. Each project has its
    own conversations, its own persistent memory store, and its own
    configuration JSON blob.

    Columns:
        id          - UUID primary key.
        name        - Human-readable display name (e.g. "My Web App").
        config_json - Arbitrary project-level settings (budget, model, tools, etc.)
                      stored as a JSON blob. Schema is validated at the application
                      layer. Example: {"budget_usd": 50, "default_model": "claude-3-5-sonnet"}.
        created_at  - UTC timestamp of project creation.
        updated_at  - UTC timestamp of last modification (updated by application layer).

    Relationships:
        conversations - All conversations belonging to this project (cascade delete).
        memory        - All key/value memory entries scoped to this project (cascade delete).
    """

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc=(
            "FK → users.id. Null for legacy/anonymous projects. "
            "SET NULL on user delete preserves project data."
        ),
    )
    circle_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("circles.id", ondelete="SET NULL"),
        nullable=True,
        doc=(
            "FK → circles.id. Null for projects not in any circle. "
            "SET NULL on circle delete preserves the project."
        ),
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Human-readable display name for the project.",
    )
    project_dir: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="",
        doc="Absolute path to the project's working directory on disk.",
    )
    config_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        default=dict,
        doc=(
            "Arbitrary project-level configuration blob. "
            "Suggested keys: budget_usd, default_model, away_mode, tags."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when the project was created.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        doc="UTC timestamp of the last modification to the project record.",
    )

    # ── Relationships ──────────────────────────────────────────────────────
    user: Mapped[User | None] = relationship(
        "User",
        back_populates="projects",
    )
    circle: Mapped[Circle | None] = relationship(
        "Circle",
        back_populates="projects",
    )
    conversations: Mapped[list[Conversation]] = relationship(
        "Conversation",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )
    memory: Mapped[list[Memory]] = relationship(
        "Memory",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )
    execution_sessions: Mapped[list[ExecutionSession]] = relationship(
        "ExecutionSession",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )
    execution_runs: Mapped[list[ExecutionRun]] = relationship(
        "ExecutionRun",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )
    chat_channels: Mapped[list[ChatChannel]] = relationship(
        "ChatChannel",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
        foreign_keys="ChatChannel.project_id",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project id={self.id!r} name={self.name!r}>"

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "idx_projects_user_id",
            "user_id",
        ),
        # Composite: list user's projects sorted by last activity (dashboard hot path)
        Index(
            "idx_projects_user_updated",
            "user_id",
            "updated_at",
        ),
    )


# ---------------------------------------------------------------------------
# Model: conversations
# ---------------------------------------------------------------------------


class Conversation(Base):
    """Represents a single conversation thread within a project.

    A conversation groups a sequence of messages and agent actions into a
    coherent context window. It maps 1:1 to a Claude SDK session at runtime
    but survives server restarts — the conversation can be resumed by
    replaying messages from this table.

    Columns:
        id             - UUID primary key.
        project_id     - FK → projects.id (CASCADE DELETE).
        title          - Optional human-readable title (e.g. "Fix login bug").
                         May be auto-generated from the first user message.
        created_at     - UTC timestamp when the conversation was created.
        last_active_at - UTC timestamp of the last message or action. Updated
                         every time a message is appended. Used for ordering
                         the conversation list and for idle-timeout detection.

    Relationships:
        project       - Parent project.
        messages      - All messages in this conversation (cascade delete).
        agent_actions - All agent actions in this conversation (cascade delete).

    Indexes:
        idx_conversations_project_active - (project_id, last_active_at DESC)
            Hot path: fetch all conversations for a project sorted by recency.
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → projects.id. Deleting a project deletes all its conversations.",
    )
    title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        doc=(
            "Human-readable title for this conversation. "
            "May be null until set by the user or auto-generated from the first message."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when this conversation was first created.",
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc=(
            "UTC timestamp of the most recent activity (message or agent action). "
            "Updated on every append to keep the conversation list sorted by recency."
        ),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    project: Mapped[Project] = relationship(
        "Project",
        back_populates="conversations",
    )
    messages: Mapped[list[Message]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.timestamp",
        lazy="select",
    )
    agent_actions: Mapped[list[AgentAction]] = relationship(
        "AgentAction",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AgentAction.timestamp",
        lazy="select",
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "idx_conversations_project_active",
            "project_id",
            "last_active_at",
            # DESC on last_active_at gives most-recent-first ordering
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Conversation id={self.id!r} project_id={self.project_id!r} title={self.title!r}>"


# ---------------------------------------------------------------------------
# Model: messages
# ---------------------------------------------------------------------------


class Message(Base):
    """Represents a single message in a conversation (user, assistant, or system).

    Messages are the primary SSOT for conversation history. They are append-only
    in normal operation — edits are not supported (create a new message instead).
    The full conversation can be reconstructed by fetching all messages ordered
    by timestamp, making this table the ground truth for LLM context replay.

    Columns:
        id              - UUID primary key.
        conversation_id - FK → conversations.id (CASCADE DELETE).
        role            - Message role: 'user' | 'assistant' | 'system' | 'tool'.
                          Matches the Claude/OpenAI message role vocabulary.
        content         - Full message content (text). May be empty string for
                          tool-result messages that only carry metadata.
        timestamp       - UTC timestamp when the message was created. Used for
                          ordering and for context-window truncation decisions.
        metadata_json   - Optional metadata blob. Suggested keys:
                            model         : str   – model used for this turn
                            input_tokens  : int   – prompt token count
                            output_tokens : int   – completion token count
                            cost_usd      : float – computed API cost
                            stop_reason   : str   – 'end_turn' | 'max_tokens' | 'tool_use'
                            tool_use_id   : str   – for role='tool' messages

    Indexes:
        idx_messages_conversation_ts - (conversation_id, timestamp ASC)
            Hot path: fetch all messages for a conversation in chronological order
            for LLM context reconstruction.
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → conversations.id. Deleting a conversation deletes all its messages.",
    )
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        doc="Message role: 'user' | 'assistant' | 'system' | 'tool'.",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        doc="Full message content as plain text.",
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when this message was appended.",
    )
    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        doc=(
            "Optional metadata blob. Keys: model, input_tokens, output_tokens, "
            "total_tokens, stop_reason, tool_use_id."
        ),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    conversation: Mapped[Conversation] = relationship(
        "Conversation",
        back_populates="messages",
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "idx_messages_conversation_ts",
            "conversation_id",
            "timestamp",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        snippet = (self.content or "")[:40].replace("\n", " ")
        return f"<Message id={self.id!r} role={self.role!r} content={snippet!r}>"


# ---------------------------------------------------------------------------
# Model: agent_actions
# ---------------------------------------------------------------------------


class AgentAction(Base):
    """Records every action taken by an agent within a conversation.

    Agent actions are the audit trail of the system's internal behavior —
    tool calls, sub-agent handoffs, planning decisions, memory reads/writes, etc.
    They are distinct from messages (which are the user-visible conversation)
    but share the same conversation scope.

    Columns:
        id              - UUID primary key.
        conversation_id - FK → conversations.id (CASCADE DELETE).
        agent_role      - Which agent performed the action:
                            'orchestrator' | 'pm' | 'memory' | 'specialist' | <custom>
        action_type     - Category of action:
                            'tool_call'   – executed a tool (bash, file, search, …)
                            'message'     – sent a sub-agent message
                            'decision'    – made a routing/planning decision
                            'handoff'     – transferred control to another agent
                            'memory_read' – read from project memory
                            'memory_write'– wrote to project memory
        payload_json    - Input to the action (tool args, message content, decision rationale).
        result_json     - Output of the action (tool output, response, error info).
                          May be null if the action is still in-progress.
        timestamp       - UTC timestamp when the action was initiated.

    Indexes:
        idx_agent_actions_conversation_ts   - (conversation_id, timestamp ASC)
            Hot path: fetch ordered action log for a conversation.
        idx_agent_actions_conversation_role - (conversation_id, agent_role)
            Used to filter actions by a specific agent within a conversation.
    """

    __tablename__ = "agent_actions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → conversations.id. Deleting a conversation deletes all its agent actions.",
    )
    agent_role: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc=(
            "Role of the agent that performed the action. "
            "E.g. 'orchestrator', 'pm', 'memory', 'specialist'."
        ),
    )
    action_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc=(
            "Category of action: 'tool_call' | 'message' | 'decision' | "
            "'handoff' | 'memory_read' | 'memory_write'."
        ),
    )
    payload_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        doc="Input/arguments for this action (tool args, message content, decision rationale).",
    )
    result_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        doc=(
            "Output/result of this action. Null if still in-progress. "
            "Should include 'success': bool and 'error': str on failure."
        ),
    )
    task_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc=(
            "DAG task ID this action belongs to. "
            "Links the agent action to a specific task in the DAG execution graph. "
            "Null for actions not associated with a specific DAG task."
        ),
    )
    round: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc=(
            "Orchestration round number (0-indexed) when this action occurred. "
            "Enables chronological ordering within a single task. "
            "Null for non-DAG actions."
        ),
    )
    cost_usd: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Deprecated — kept for backward compat with existing rows. "
            "Prefer input_tokens/output_tokens/total_tokens columns."
        ),
    )
    input_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=0,
        doc="Number of input (prompt) tokens consumed by this action's API call.",
    )
    output_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=0,
        doc="Number of output (completion) tokens produced by this action's API call.",
    )
    total_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=0,
        doc="Total tokens (input + output) consumed by this action.",
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when this action was initiated.",
    )

    # ── Relationships ──────────────────────────────────────────────────────
    conversation: Mapped[Conversation] = relationship(
        "Conversation",
        back_populates="agent_actions",
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "idx_agent_actions_conversation_ts",
            "conversation_id",
            "timestamp",
        ),
        Index(
            "idx_agent_actions_conversation_role",
            "conversation_id",
            "agent_role",
        ),
        Index(
            "idx_agent_actions_task_id",
            "task_id",
        ),
        # Composite: task-scoped agent history queries (get_project_history groups by task+role)
        Index(
            "idx_agent_actions_task_role_ts",
            "task_id",
            "agent_role",
            "timestamp",
        ),
        # Composite: conversation + action_type for filtered aggregations
        Index(
            "idx_agent_actions_conv_type",
            "conversation_id",
            "action_type",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentAction id={self.id!r} agent_role={self.agent_role!r} "
            f"action_type={self.action_type!r}>"
        )


# ---------------------------------------------------------------------------
# Model: memory
# ---------------------------------------------------------------------------


class Memory(Base):
    """Persistent key/value memory store scoped to a project.

    The memory table acts as the project-level key/value store that agents
    can read and write to persist facts, preferences, and learned context
    across conversations. Unlike messages (which are conversation-scoped),
    memory is project-scoped and survives across conversation boundaries.

    Columns:
        id         - UUID primary key.
        project_id - FK → projects.id (CASCADE DELETE).
        key        - String key for the memory entry. Convention: use dot-notation
                     namespacing, e.g. 'user.name', 'project.tech_stack',
                     'agent.orchestrator.last_plan'. Max 500 chars.
        value_json - Arbitrary JSON value. Can be a string, number, array,
                     or nested object. Null is valid (used to soft-clear a key).
        updated_at - UTC timestamp of the last write. Useful for staleness detection.

    Constraints:
        UNIQUE(project_id, key) — enforces one value per key per project.
            Use upsert (INSERT ... ON CONFLICT DO UPDATE) for writes.

    Indexes:
        idx_memory_project_key - (project_id, key)
            Covers both the UNIQUE constraint and the hot read path
            (look up a key for a given project).
    """

    __tablename__ = "memory"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → projects.id. Deleting a project deletes all its memory entries.",
    )
    key: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        doc=(
            "Memory key using dot-notation namespacing. "
            "E.g. 'user.name', 'project.tech_stack', 'agent.orchestrator.last_plan'."
        ),
    )
    value_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSON,
        nullable=True,
        doc="Arbitrary JSON value. Use upsert semantics on write.",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=_utcnow,
        doc="UTC timestamp when this memory entry was first created. NULL for rows created before this column existed.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        doc="UTC timestamp of the last write to this memory entry.",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        doc=(
            "Optimistic lock version counter. Incremented on every write. "
            "Concurrent writers must include version in their WHERE clause "
            "and retry on zero-rows-affected (stale read)."
        ),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    project: Mapped[Project] = relationship(
        "Project",
        back_populates="memory",
    )

    # ── Constraints & Indexes ──────────────────────────────────────────────
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_memory_project_key"),
        Index(
            "idx_memory_project_key",
            "project_id",
            "key",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Memory id={self.id!r} project_id={self.project_id!r} key={self.key!r}>"


# ---------------------------------------------------------------------------
# Model: execution_sessions
# ---------------------------------------------------------------------------


class ExecutionSession(Base):
    """Represents a single DAG execution run within a project.

    An execution session groups all agent actions, conversations, and artifacts
    produced during one orchestration run (e.g. "Add JWT auth"). It provides
    the top-level grouping for project history — each time the user starts the
    orchestrator, a new ExecutionSession is created.

    Columns:
        id           - UUID primary key.
        project_id   - FK → projects.id (CASCADE DELETE).
        title        - Human-readable title (typically the user's prompt).
        status       - 'running' | 'completed' | 'failed' | 'cancelled'.
        prompt       - The original user prompt that initiated this session.
        plan_json    - The DAG plan as JSON (tasks, dependencies, etc.).
        summary      - Auto-generated summary of what was accomplished.
        total_tasks  - Total number of tasks in the plan.
        completed_tasks - Number of tasks that completed successfully.
        failed_tasks - Number of tasks that failed.
        total_input_tokens  - Aggregate input tokens across all agents.
        total_output_tokens - Aggregate output tokens across all agents.
        total_tokens - Aggregate total tokens across all agents.
        started_at   - UTC timestamp when execution began.
        completed_at - UTC timestamp when execution finished (null if running).
        created_at   - UTC timestamp when the record was created.

    Indexes:
        idx_exec_sessions_project_started - (project_id, started_at DESC)
            Hot path: fetch all sessions for a project sorted by recency.
    """

    __tablename__ = "execution_sessions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → projects.id. Deleting a project deletes all its execution sessions.",
    )
    title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        doc="Human-readable title derived from the user prompt.",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="running",
        doc="Session status: 'running' | 'completed' | 'failed' | 'cancelled'.",
    )
    prompt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="The original user prompt that initiated this execution.",
    )
    plan_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        doc="The DAG execution plan as a JSON blob (tasks, deps, roles, etc.).",
    )
    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Auto-generated summary of what was accomplished in this session.",
    )
    total_tasks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Total number of tasks in the execution plan.",
    )
    completed_tasks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Number of tasks that completed successfully.",
    )
    failed_tasks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Number of tasks that failed.",
    )
    total_input_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Aggregate input tokens consumed across all agents in this session.",
    )
    total_output_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Aggregate output tokens produced across all agents in this session.",
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Aggregate total tokens consumed across all agents in this session.",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when the execution started.",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp when the execution finished. Null if still running.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when this record was created.",
    )

    # ── Relationships ──────────────────────────────────────────────────────
    project: Mapped[Project] = relationship(
        "Project",
        back_populates="execution_sessions",
    )
    runs: Mapped[list[ExecutionRun]] = relationship(
        "ExecutionRun",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ExecutionRun.started_at",
        lazy="select",
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "idx_exec_sessions_project_started",
            "project_id",
            "started_at",
        ),
        Index(
            "idx_exec_sessions_project_status",
            "project_id",
            "status",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ExecutionSession id={self.id!r} project_id={self.project_id!r} "
            f"status={self.status!r}>"
        )


# ---------------------------------------------------------------------------
# Model: execution_runs
# ---------------------------------------------------------------------------


class ExecutionRun(Base):
    """Tracks an individual task execution within a DAG execution session.

    Each row represents one task's lifecycle within a broader DAG run. This
    provides fine-grained history for the dashboard — which tasks ran, in what
    order, what artifacts they produced, and how they completed.

    Columns:
        id             - UUID primary key.
        session_id     - FK → execution_sessions.id (CASCADE DELETE).
        project_id     - FK → projects.id (CASCADE DELETE). Denormalized for
                         direct project-scoped queries without joining sessions.
        task_id        - DAG task identifier (e.g. 'task_003').
        task_name      - Human-readable task name / goal summary.
        role           - Agent role assigned to this task (e.g. 'backend_developer').
        status         - 'pending' | 'running' | 'completed' | 'failed' | 'skipped'.
        depends_on_json- List of task_ids this task depends on.
        artifacts_json - Artifacts produced by this task (files, schemas, etc.).
        error_message  - Error details if the task failed.
        input_tokens   - Prompt tokens consumed by this task.
        output_tokens  - Completion tokens produced by this task.
        total_tokens   - Total tokens consumed by this task.
        started_at     - UTC timestamp when this task began execution.
        completed_at   - UTC timestamp when this task finished. Null if running.
        created_at     - UTC timestamp when the record was created.

    Indexes:
        idx_exec_runs_session_id      - (session_id) for session lookups.
        idx_exec_runs_project_status  - (project_id, status) for concurrent queries.
        idx_exec_runs_session_status  - (session_id, status) for in-session filtering.
    """

    __tablename__ = "execution_runs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("execution_sessions.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → execution_sessions.id. Deleting a session deletes all its runs.",
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        doc=(
            "FK → projects.id. Denormalized from session for direct "
            "project-scoped queries without joining execution_sessions."
        ),
    )
    task_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="DAG task identifier (e.g. 'task_003').",
    )
    task_name: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        doc="Human-readable task name or goal summary.",
    )
    role: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Agent role assigned to this task (e.g. 'backend_developer', 'pm').",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pending",
        doc="Task status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped'.",
    )
    depends_on_json: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        doc="List of task_ids this task depends on in the DAG.",
    )
    artifacts_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        doc=(
            "Artifacts produced by this task. Structure: "
            "{'files': [...], 'schemas': [...], 'type': 'code'|'schema'|'plan'|...}"
        ),
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Error details if the task failed. Null on success.",
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Input (prompt) tokens consumed by this task's agent.",
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Output (completion) tokens produced by this task's agent.",
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Total tokens consumed by this task.",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp when this task began execution. Null if pending.",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp when this task finished. Null if still running/pending.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when this record was created.",
    )

    # ── Relationships ──────────────────────────────────────────────────────
    session: Mapped[ExecutionSession] = relationship(
        "ExecutionSession",
        back_populates="runs",
    )
    project: Mapped[Project] = relationship(
        "Project",
        back_populates="execution_runs",
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "idx_exec_runs_session_id",
            "session_id",
        ),
        Index(
            "idx_exec_runs_project_status",
            "project_id",
            "status",
        ),
        Index(
            "idx_exec_runs_session_status",
            "session_id",
            "status",
        ),
        # Composite: chronological task listing within a project
        Index(
            "idx_exec_runs_project_started",
            "project_id",
            "started_at",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ExecutionRun id={self.id!r} task_id={self.task_id!r} "
            f"status={self.status!r}>"
        )


# ---------------------------------------------------------------------------
# Model: circles
# ---------------------------------------------------------------------------


class Circle(Base):
    """A collaborative group/workspace that contains projects and members.

    Circles provide a team-level grouping above projects. Each circle has
    members with roles (owner, admin, member, viewer), shared settings,
    and can contain multiple projects and chat channels.

    Columns:
        id            - UUID primary key.
        name          - Human-readable circle name (e.g. "Backend Team").
        description   - Optional description of the circle's purpose.
        avatar_url    - Optional URL to the circle's avatar image.
        settings_json - Arbitrary circle-level settings (notification prefs, etc.).
        created_by    - FK → users.id. The user who created this circle.
        created_at    - UTC timestamp of circle creation.
        updated_at    - UTC timestamp of last modification.
    """

    __tablename__ = "circles"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Human-readable display name for the circle.",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Optional description of the circle's purpose.",
    )
    avatar_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        doc="Optional URL to the circle's avatar image.",
    )
    settings_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        default=dict,
        doc="Arbitrary circle-level settings (notification prefs, default model, etc.).",
    )
    created_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → users.id. The user who created this circle.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when the circle was created.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        doc="UTC timestamp of the last modification to the circle.",
    )

    # ── Relationships ──────────────────────────────────────────────────────
    creator: Mapped[User] = relationship(
        "User",
        foreign_keys=[created_by],
    )
    projects: Mapped[list[Project]] = relationship(
        "Project",
        back_populates="circle",
        lazy="select",
    )
    members: Mapped[list[CircleMember]] = relationship(
        "CircleMember",
        back_populates="circle",
        cascade="all, delete-orphan",
        lazy="select",
    )
    invitations: Mapped[list[CircleInvitation]] = relationship(
        "CircleInvitation",
        back_populates="circle",
        cascade="all, delete-orphan",
        lazy="select",
    )
    chat_channels: Mapped[list[ChatChannel]] = relationship(
        "ChatChannel",
        back_populates="circle",
        cascade="all, delete-orphan",
        lazy="select",
        foreign_keys="ChatChannel.circle_id",
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("idx_circles_created_by", "created_by"),
        Index("idx_circles_name", "name"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Circle id={self.id!r} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Model: circle_members
# ---------------------------------------------------------------------------


class CircleMember(Base):
    """Tracks membership and roles within a circle.

    Roles:
        owner  - Full control, can delete the circle.
        admin  - Can manage members and settings.
        member - Can view and contribute to projects.
        viewer - Read-only access.

    Columns:
        id         - UUID primary key.
        circle_id  - FK → circles.id (CASCADE DELETE).
        user_id    - FK → users.id (CASCADE DELETE).
        role       - 'owner' | 'admin' | 'member' | 'viewer'.
        joined_at  - UTC timestamp when the user joined.
        updated_at - UTC timestamp of last role change.
    """

    __tablename__ = "circle_members"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    circle_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("circles.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → circles.id. Deleting a circle removes all memberships.",
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → users.id. Deleting a user removes all their memberships.",
    )
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="member",
        doc="Membership role: 'owner' | 'admin' | 'member' | 'viewer'.",
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when the user joined this circle.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        doc="UTC timestamp of the last role change.",
    )

    # ── Relationships ──────────────────────────────────────────────────────
    circle: Mapped[Circle] = relationship(
        "Circle",
        back_populates="members",
    )
    user: Mapped[User] = relationship(
        "User",
        back_populates="circle_memberships",
    )

    # ── Constraints & Indexes ──────────────────────────────────────────────
    __table_args__ = (
        UniqueConstraint("circle_id", "user_id", name="uq_circle_member"),
        Index("idx_circle_members_circle_id", "circle_id"),
        Index("idx_circle_members_user_id", "user_id"),
        Index("idx_circle_members_circle_role", "circle_id", "role"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CircleMember circle_id={self.circle_id!r} user_id={self.user_id!r} role={self.role!r}>"


# ---------------------------------------------------------------------------
# Model: circle_invitations
# ---------------------------------------------------------------------------


class CircleInvitation(Base):
    """Tracks pending, accepted, and declined invitations to circles.

    Columns:
        id              - UUID primary key.
        circle_id       - FK → circles.id (CASCADE DELETE).
        invited_user_id - FK → users.id (CASCADE DELETE). The invitee.
        invited_by_id   - FK → users.id (SET NULL). The inviter.
        role            - Role to be assigned on acceptance.
        status          - 'pending' | 'accepted' | 'declined' | 'expired'.
        invite_token    - Unique token for email/link-based invitations.
        expires_at      - UTC timestamp when the invitation expires.
        created_at      - UTC timestamp when the invitation was created.
        updated_at      - UTC timestamp of the last status change.
    """

    __tablename__ = "circle_invitations"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    circle_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("circles.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → circles.id. Deleting a circle removes all pending invitations.",
    )
    invited_user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → users.id. The user being invited.",
    )
    invited_by_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="FK → users.id. The user who sent the invitation. SET NULL on delete.",
    )
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="member",
        doc="Role to assign on acceptance: 'admin' | 'member' | 'viewer'.",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pending",
        doc="Invitation status: 'pending' | 'accepted' | 'declined' | 'expired'.",
    )
    invite_token: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        default=_new_uuid,
        doc="Unique token for email/link-based invitation acceptance.",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp when the invitation expires. Null for no expiry.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when the invitation was created.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        doc="UTC timestamp of the last status change.",
    )

    # ── Relationships ──────────────────────────────────────────────────────
    circle: Mapped[Circle] = relationship(
        "Circle",
        back_populates="invitations",
    )
    invited_user: Mapped[User] = relationship(
        "User",
        back_populates="circle_invitations",
        foreign_keys=[invited_user_id],
    )
    invited_by: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[invited_by_id],
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("idx_circle_invitations_circle_id", "circle_id"),
        Index("idx_circle_invitations_user_id", "invited_user_id"),
        Index("idx_circle_invitations_circle_status", "circle_id", "status"),
        Index("idx_circle_invitations_token", "invite_token"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CircleInvitation id={self.id!r} circle_id={self.circle_id!r} "
            f"status={self.status!r}>"
        )


# ---------------------------------------------------------------------------
# Model: chat_channels
# ---------------------------------------------------------------------------


class ChatChannel(Base):
    """A chat channel scoped to a circle or project.

    Channels can be:
    - Circle-scoped: General discussion for a circle (circle_id set, project_id null).
    - Project-scoped: Discussion about a specific project (both circle_id and project_id set,
      or just project_id for standalone project channels).
    - Direct message: channel_type='dm', neither circle_id nor project_id required.

    Columns:
        id           - UUID primary key.
        circle_id    - FK → circles.id (CASCADE DELETE). Null for non-circle channels.
        project_id   - FK → projects.id (CASCADE DELETE). Null for circle-level channels.
        name         - Channel display name (e.g. "#general", "#backend").
        channel_type - 'circle' | 'project' | 'dm'.
        description  - Optional channel description/topic.
        is_archived  - Whether the channel is archived (read-only).
        created_by   - FK → users.id. The user who created the channel.
        created_at   - UTC timestamp of channel creation.
        updated_at   - UTC timestamp of last modification.
    """

    __tablename__ = "chat_channels"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    circle_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("circles.id", ondelete="CASCADE"),
        nullable=True,
        doc="FK → circles.id. Null for non-circle channels.",
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        doc="FK → projects.id. Null for circle-level or DM channels.",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Channel display name (e.g. '#general', '#backend').",
    )
    channel_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="circle",
        doc="Channel type: 'circle' | 'project' | 'dm'.",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Optional channel description or topic.",
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        doc="Whether the channel is archived (read-only).",
    )
    created_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="FK → users.id. The user who created this channel.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when the channel was created.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        doc="UTC timestamp of the last modification.",
    )

    # ── Relationships ──────────────────────────────────────────────────────
    circle: Mapped[Circle | None] = relationship(
        "Circle",
        back_populates="chat_channels",
        foreign_keys=[circle_id],
    )
    project: Mapped[Project | None] = relationship(
        "Project",
        back_populates="chat_channels",
        foreign_keys=[project_id],
    )
    creator: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[created_by],
    )
    messages: Mapped[list[ChatMessage]] = relationship(
        "ChatMessage",
        back_populates="channel",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
        lazy="select",
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("idx_chat_channels_circle_id", "circle_id"),
        Index("idx_chat_channels_project_id", "project_id"),
        Index("idx_chat_channels_type", "channel_type"),
        Index("idx_chat_channels_circle_type", "circle_id", "channel_type"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChatChannel id={self.id!r} name={self.name!r} type={self.channel_type!r}>"


# ---------------------------------------------------------------------------
# Model: chat_messages
# ---------------------------------------------------------------------------


class ChatMessage(Base):
    """A single message within a chat channel, with optional threading.

    Messages support threading via parent_message_id — a self-referencing FK
    that creates a tree structure. Top-level messages have parent_message_id=null.

    Columns:
        id                 - UUID primary key.
        channel_id         - FK → chat_channels.id (CASCADE DELETE).
        sender_id          - FK → users.id (CASCADE DELETE).
        parent_message_id  - FK → chat_messages.id (SET NULL). For threading.
        content            - Message content as plain text / markdown.
        message_type       - 'text' | 'system' | 'file' | 'code'.
        metadata_json      - Optional metadata (file attachments, mentions, etc.).
        is_edited          - Whether the message has been edited.
        deleted_at         - Soft delete timestamp. Null if not deleted.
        created_at         - UTC timestamp of message creation.
        updated_at         - UTC timestamp of last edit.
    """

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    channel_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_channels.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → chat_channels.id. Deleting a channel deletes all its messages.",
    )
    sender_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → users.id. The user who sent this message.",
    )
    parent_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
        doc="FK → chat_messages.id. Self-ref for threading. Null for top-level messages.",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        doc="Message content as plain text or markdown.",
    )
    message_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="text",
        doc="Message type: 'text' | 'system' | 'file' | 'code'.",
    )
    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        doc="Optional metadata: file attachments, mentions, reactions, etc.",
    )
    is_edited: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        doc="Whether this message has been edited after creation.",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Soft delete timestamp. Null if the message is active.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when the message was sent.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        doc="UTC timestamp of the last edit.",
    )

    # ── Relationships ──────────────────────────────────────────────────────
    channel: Mapped[ChatChannel] = relationship(
        "ChatChannel",
        back_populates="messages",
    )
    sender: Mapped[User] = relationship(
        "User",
        back_populates="chat_messages",
    )
    parent_message: Mapped[ChatMessage | None] = relationship(
        "ChatMessage",
        remote_side="ChatMessage.id",
        foreign_keys=[parent_message_id],
        lazy="select",
    )
    read_receipts: Mapped[list[MessageReadReceipt]] = relationship(
        "MessageReadReceipt",
        back_populates="message",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("idx_chat_messages_channel_created", "channel_id", "created_at"),
        Index("idx_chat_messages_sender_id", "sender_id"),
        Index("idx_chat_messages_parent_id", "parent_message_id"),
        Index(
            "idx_chat_messages_channel_active",
            "channel_id",
            "created_at",
            postgresql_where="deleted_at IS NULL",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        snippet = (self.content or "")[:40].replace("\n", " ")
        return f"<ChatMessage id={self.id!r} sender_id={self.sender_id!r} content={snippet!r}>"


# ---------------------------------------------------------------------------
# Model: message_read_receipts
# ---------------------------------------------------------------------------


class MessageReadReceipt(Base):
    """Tracks which user has read which message for read receipt indicators.

    One row per (message, user) pair. The existence of the row means the
    user has seen the message.

    Columns:
        id         - UUID primary key.
        message_id - FK → chat_messages.id (CASCADE DELETE).
        user_id    - FK → users.id (CASCADE DELETE).
        read_at    - UTC timestamp when the user read the message.
    """

    __tablename__ = "message_read_receipts"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
        doc="UUID primary key.",
    )
    message_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → chat_messages.id. Deleting a message removes its read receipts.",
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        doc="FK → users.id. Deleting a user removes their read receipts.",
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        doc="UTC timestamp when the user read this message.",
    )

    # ── Relationships ──────────────────────────────────────────────────────
    message: Mapped[ChatMessage] = relationship(
        "ChatMessage",
        back_populates="read_receipts",
    )
    user: Mapped[User] = relationship(
        "User",
        back_populates="message_read_receipts",
    )

    # ── Constraints & Indexes ──────────────────────────────────────────────
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_read_receipt_message_user"),
        Index("idx_read_receipts_message_id", "message_id"),
        Index("idx_read_receipts_user_id", "user_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MessageReadReceipt message_id={self.message_id!r} user_id={self.user_id!r}>"


# ---------------------------------------------------------------------------
# Convenience exports
# ---------------------------------------------------------------------------

__all__ = [
    "AgentAction",
    "Base",
    "ChatChannel",
    "ChatMessage",
    "Circle",
    "CircleInvitation",
    "CircleMember",
    "Conversation",
    "ExecutionRun",
    "ExecutionSession",
    "Memory",
    "Message",
    "MessageReadReceipt",
    "Project",
    "User",
]
