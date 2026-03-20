"""Centralised validation limits and magic numbers for the dashboard API.

Every hard-coded constraint used in Pydantic models, Query parameters,
or inline validation lives here so they can be reviewed and tuned in
one place without hunting through router code.
"""

# ---------------------------------------------------------------------------
# Project fields
# ---------------------------------------------------------------------------
PROJECT_NAME_MAX_LENGTH: int = 200
PROJECT_DIR_MAX_LENGTH: int = 1000
PROJECT_DESCRIPTION_MAX_LENGTH: int = 2000
PROJECT_AGENTS_MIN: int = 1
PROJECT_AGENTS_MAX: int = 20

# ---------------------------------------------------------------------------
# Settings / orchestration limits
# ---------------------------------------------------------------------------
MAX_TURNS_LIMIT: int = 10_000
MAX_BUDGET_LIMIT: float = 10_000
AGENT_TIMEOUT_MIN_SECONDS: int = 30
AGENT_TIMEOUT_MAX_SECONDS: int = 7_200
MAX_USER_MESSAGE_LENGTH_MIN: int = 100
MAX_USER_MESSAGE_LENGTH_MAX: int = 100_000
MAX_ORCHESTRATOR_LOOPS_LIMIT: int = 10_000

# ---------------------------------------------------------------------------
# Task description
# ---------------------------------------------------------------------------
TASK_DESCRIPTION_MAX_LENGTH: int = 2_000

# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
MESSAGES_DEFAULT_LIMIT: int = 50
MESSAGES_MAX_LIMIT: int = 500
ACTIVITY_DEFAULT_LIMIT: int = 200
ACTIVITY_MAX_LIMIT: int = 1_000
