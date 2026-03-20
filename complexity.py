"""Unified role classification and complexity analysis.

Single source of truth for:
- WRITER_ROLES / READER_ROLES — which agent roles modify files vs. read-only
- classify_complexity() — re-exported from blackboard for convenience

All other modules should import role sets from here instead of defining
their own copies.
"""

from __future__ import annotations

from contracts import AgentRole

# ---------------------------------------------------------------------------
# Role classification — single source of truth
#
# WRITER_ROLES:  agents that create/modify project files.
#                Must be scheduled with file-lock awareness.
# READER_ROLES:  analysis-only agents.  Safe to run in parallel with anything.
#
# Both sets are provided as:
#   - AgentRole enum sets  (for dag_executor / typed code)
#   - str sets             (for orchestrator / string-keyed code)
# ---------------------------------------------------------------------------

WRITER_ROLES_ENUM: frozenset[AgentRole] = frozenset(
    {
        AgentRole.FRONTEND_DEVELOPER,
        AgentRole.BACKEND_DEVELOPER,
        AgentRole.DATABASE_EXPERT,
        AgentRole.DEVOPS,
        AgentRole.TYPESCRIPT_ARCHITECT,
        AgentRole.PYTHON_BACKEND,
        AgentRole.DEVELOPER,
    }
)
"""Agent roles that write/modify files — must run sequentially when file scopes overlap."""

READER_ROLES_ENUM: frozenset[AgentRole] = frozenset(
    {
        AgentRole.RESEARCHER,
        AgentRole.REVIEWER,
        AgentRole.SECURITY_AUDITOR,
        AgentRole.UX_CRITIC,
        AgentRole.TEST_ENGINEER,
        AgentRole.TESTER,
        AgentRole.MEMORY,
    }
)
"""Read-only / analysis agents — always safe to run in parallel."""

# String versions derived from the enum sets (used by orchestrator.py)
WRITER_ROLES: frozenset[str] = frozenset(r.value for r in WRITER_ROLES_ENUM)
READER_ROLES: frozenset[str] = frozenset(r.value for r in READER_ROLES_ENUM)

# Re-export classify_complexity so callers can do:
#   from complexity import classify_complexity
from blackboard import ComplexityLevel, ComplexityResult, classify_complexity  # noqa: F401
