"""Prompt Enhancer — injects output schemas, few-shot examples, and
inter-agent contracts into agent prompts.

Based on research from Augment Code (SWE-bench #1) and DSPy:
1. Agents need explicit output format specification
2. Few-shot examples dramatically improve output consistency
3. Inter-agent contracts (what downstream expects) improve completeness
4. Middle truncation preserves more useful context than suffix truncation

This module wraps the existing prompt system (prompts.py) without
modifying it, adding structured enhancements at injection time.

Integration:
    Called from ``orch_context.py`` or ``dag_executor.py`` when building
    the final prompt for an agent. Enhances the prompt with:
    - Output schema (what format to return)
    - Few-shot example of good output
    - Downstream contract (what the next agent expects)
    - Self-check checklist
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── Output Schemas per Role ──────────────────────────────────────────────

OUTPUT_SCHEMAS: dict[str, str] = {
    "backend_developer": """
<output_format>
When you finish, your output MUST include these clearly marked sections:

## FILES CREATED/MODIFIED
List every file you created or modified, one per line:
- path/to/file.py — brief description of what it does

## KEY DECISIONS
List 2-3 important design decisions you made and WHY:
- Decision: [what] — Reason: [why]

## VERIFICATION
Exact commands to verify your work:
```bash
# How to run
# How to test
# Expected output
```

## KNOWN LIMITATIONS
Anything the next agent should know:
- [limitation or TODO]
</output_format>""",

    "frontend_developer": """
<output_format>
When you finish, your output MUST include these clearly marked sections:

## FILES CREATED/MODIFIED
List every file you created or modified, one per line:
- path/to/Component.tsx — brief description

## COMPONENT HIERARCHY
Show the component tree you built:
- App → Layout → [YourComponents]

## VERIFICATION
How to verify your work:
```bash
# How to run dev server
# What to check in browser
# Expected behavior
```

## KNOWN LIMITATIONS
- [limitation or TODO]
</output_format>""",

    "database_expert": """
<output_format>
When you finish, your output MUST include these clearly marked sections:

## SCHEMA
Show the final schema (CREATE TABLE statements or model definitions).

## FILES CREATED/MODIFIED
- path/to/file.py — description

## INDEXES
List all indexes created and WHY each one is needed.

## MIGRATION
If applicable, how to run the migration:
```bash
# migration command
```

## KNOWN LIMITATIONS
- [limitation or TODO]
</output_format>""",

    "test_engineer": """
<output_format>
When you finish, your output MUST include these clearly marked sections:

## TEST SUMMARY
| Test | What it tests | Status |
|------|--------------|--------|
| test_name | description | PASS/FAIL |

## FILES CREATED/MODIFIED
- path/to/test_file.py — description

## COVERAGE
What is covered and what is NOT covered.

## HOW TO RUN
```bash
# exact pytest command
# expected output
```
</output_format>""",

    "reviewer": """
<output_format>
Your review MUST include these clearly marked sections:

## CRITICAL ISSUES (must fix before merge)
1. [File:Line] Issue description — Suggested fix

## WARNINGS (should fix)
1. [File:Line] Issue description — Suggested fix

## POSITIVE OBSERVATIONS
1. What was done well

## VERDICT
APPROVE / REQUEST_CHANGES / NEEDS_DISCUSSION

## SUMMARY
2-3 sentences overall assessment.
</output_format>""",

    "security_auditor": """
<output_format>
Your audit MUST include these clearly marked sections:

## CRITICAL VULNERABILITIES (severity: HIGH)
1. [CWE-XXX] Description — File:Line — Remediation

## MEDIUM RISK
1. Description — File:Line — Remediation

## LOW RISK / INFORMATIONAL
1. Description

## OWASP CHECKLIST
| Category | Status | Notes |
|----------|--------|-------|
| Injection | PASS/FAIL | details |
| Auth | PASS/FAIL | details |
| XSS | PASS/FAIL | details |
| CSRF | PASS/FAIL | details |
| Secrets | PASS/FAIL | details |

## VERDICT
SECURE / NEEDS_REMEDIATION / CRITICAL_BLOCK
</output_format>""",

    "devops_engineer": """
<output_format>
When you finish, your output MUST include these clearly marked sections:

## FILES CREATED/MODIFIED
- path/to/Dockerfile — description
- path/to/docker-compose.yml — description

## DEPLOYMENT STEPS
```bash
# step-by-step deployment commands
```

## ENVIRONMENT VARIABLES
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|

## HEALTH CHECK
How to verify the deployment is working.

## KNOWN LIMITATIONS
- [limitation or TODO]
</output_format>""",
}


# ── Few-Shot Examples per Role ───────────────────────────────────────────

FEW_SHOT_EXAMPLES: dict[str, str] = {
    "backend_developer": """
<example_output>
Here is an example of GOOD output from a backend developer agent:

I implemented the user authentication endpoints for the TaskFlow API.

## FILES CREATED/MODIFIED
- src/api/auth.py — JWT authentication endpoints (login, register, refresh)
- src/models/user.py — User Pydantic model with password hashing
- src/middleware/auth.py — Authentication dependency for protected routes
- src/config.py — Added JWT_SECRET and TOKEN_EXPIRY settings

## KEY DECISIONS
- Decision: Used python-jose for JWT — Reason: Lighter than PyJWT, supports RS256 for future key rotation
- Decision: Bcrypt for password hashing — Reason: Industry standard, configurable work factor
- Decision: Refresh tokens stored in DB — Reason: Allows revocation on logout/password change

## VERIFICATION
```bash
# Start the server
uvicorn src.main:app --reload

# Register a user
curl -X POST http://localhost:8000/auth/register -H "Content-Type: application/json" -d '{"email":"test@test.com","password":"Test123!"}'
# Expected: {"id": "...", "email": "test@test.com", "created_at": "..."}

# Login
curl -X POST http://localhost:8000/auth/login -H "Content-Type: application/json" -d '{"email":"test@test.com","password":"Test123!"}'
# Expected: {"access_token": "...", "refresh_token": "...", "token_type": "bearer"}
```

## KNOWN LIMITATIONS
- No rate limiting on login endpoint yet (should be added by security task)
- Email validation is basic regex — no MX record check
</example_output>""",

    "reviewer": """
<example_output>
Here is an example of GOOD output from a code reviewer agent:

## CRITICAL ISSUES (must fix before merge)
1. [src/api/tasks.py:45] SQL injection via f-string in query — Use parameterized queries: `cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))`
2. [src/api/auth.py:23] JWT secret hardcoded as "mysecret" — Move to environment variable: `os.getenv("JWT_SECRET")`

## WARNINGS (should fix)
1. [src/models/task.py:12] `status` field accepts any string — Use Literal["todo","in_progress","done"] or Enum
2. [src/api/tasks.py:78] No pagination on GET /tasks — Will timeout with large datasets. Add `limit` and `offset` params.
3. [tests/test_api.py:5] Tests use production database — Use a test fixture with temporary DB

## POSITIVE OBSERVATIONS
1. Clean separation of concerns between routes, models, and database layer
2. Good use of Pydantic for request validation
3. Comprehensive error messages in 4xx responses

## VERDICT
REQUEST_CHANGES

## SUMMARY
The API structure is solid and well-organized, but there are two critical security issues (SQL injection and hardcoded secret) that must be fixed before merge. The code quality is otherwise good, with clean Pydantic models and proper HTTP status codes.
</example_output>""",
}


# ── Downstream Contracts ─────────────────────────────────────────────────

DOWNSTREAM_CONTRACTS: dict[str, dict[str, str]] = {
    "database_expert": {
        "backend_developer": (
            "The backend developer who runs AFTER you needs:\n"
            "1. A working database initialization function they can import\n"
            "2. Clear table names and column names they can reference in queries\n"
            "3. Any connection string or configuration they need to set up\n"
            "If you don't provide these, the backend developer will be blocked."
        ),
    },
    "backend_developer": {
        "test_engineer": (
            "The test engineer who runs AFTER you needs:\n"
            "1. A running FastAPI app they can import (e.g., `from main import app`)\n"
            "2. Clear endpoint paths and expected request/response formats\n"
            "3. Any setup steps (database init, env vars) needed before testing\n"
            "If your endpoints aren't importable, the tester cannot write tests."
        ),
        "reviewer": (
            "The code reviewer who runs AFTER you will check:\n"
            "1. SQL injection vulnerabilities — use parameterized queries\n"
            "2. Hardcoded secrets — use os.getenv()\n"
            "3. Missing error handling — every endpoint needs try/except\n"
            "4. Missing input validation — use Pydantic models\n"
            "Address these proactively to avoid review-fix cycles."
        ),
        "security_auditor": (
            "The security auditor who runs AFTER you will check:\n"
            "1. Authentication implementation\n"
            "2. Input sanitization\n"
            "3. OWASP Top 10 vulnerabilities\n"
            "4. Secrets management\n"
            "Write secure code from the start to pass the audit."
        ),
    },
}


# ── Context Truncation (Middle-Out) ──────────────────────────────────────

def truncate_middle(text: str, max_chars: int) -> str:
    """Truncate text by removing the middle, preserving start and end.

    Based on Augment Code research: useful info is more likely at the
    beginning (imports, function signatures) and end (error messages,
    stack traces) than in the middle.
    """
    if len(text) <= max_chars:
        return text

    # Keep 40% from start, 40% from end, 20% for separator
    keep_chars = max_chars - 50  # Reserve space for separator
    start_len = int(keep_chars * 0.4)
    end_len = int(keep_chars * 0.4)

    start = text[:start_len]
    end = text[-end_len:]
    removed = len(text) - start_len - end_len

    return f"{start}\n\n... [{removed} characters truncated from middle] ...\n\n{end}"


# ── Main Enhancement Function ────────────────────────────────────────────

@dataclass
class PromptEnhancement:
    """Collected enhancements to inject into an agent prompt."""
    output_schema: str = ""
    few_shot_example: str = ""
    downstream_contract: str = ""
    self_check: str = ""


def build_enhancement(
    role: str,
    downstream_roles: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
) -> PromptEnhancement:
    """Build prompt enhancements for a given agent role.

    Args:
        role: The agent's role (e.g., "backend_developer").
        downstream_roles: Roles of agents that depend on this agent's output.
        acceptance_criteria: Explicit acceptance criteria for this task.

    Returns:
        PromptEnhancement with all applicable enhancements.
    """
    enhancement = PromptEnhancement()

    # 1. Output schema
    role_key = role.lower().replace(" ", "_")
    if role_key in OUTPUT_SCHEMAS:
        enhancement.output_schema = OUTPUT_SCHEMAS[role_key]

    # 2. Few-shot example
    if role_key in FEW_SHOT_EXAMPLES:
        enhancement.few_shot_example = FEW_SHOT_EXAMPLES[role_key]

    # 3. Downstream contract
    if downstream_roles and role_key in DOWNSTREAM_CONTRACTS:
        contracts = []
        for ds_role in downstream_roles:
            ds_key = ds_role.lower().replace(" ", "_")
            if ds_key in DOWNSTREAM_CONTRACTS[role_key]:
                contracts.append(DOWNSTREAM_CONTRACTS[role_key][ds_key])
        if contracts:
            enhancement.downstream_contract = (
                "\n<downstream_expectations>\n"
                "IMPORTANT — Agents that run AFTER you expect specific things from your output:\n\n"
                + "\n\n".join(contracts)
                + "\n</downstream_expectations>"
            )

    # 4. Self-check
    if acceptance_criteria:
        checks = "\n".join(f"  [ ] {c}" for c in acceptance_criteria)
        enhancement.self_check = (
            "\n<self_check>\n"
            "Before declaring your task complete, verify EACH of these:\n"
            f"{checks}\n"
            "If any checkbox is NOT met, fix it before finishing.\n"
            "</self_check>"
        )

    return enhancement


def inject_enhancements(prompt: str, enhancement: PromptEnhancement) -> str:
    """Inject enhancements into an existing prompt string.

    Appends enhancements at the end of the prompt, which is where
    models pay the most attention (after user message).
    """
    parts = [prompt]

    if enhancement.output_schema:
        parts.append(enhancement.output_schema)

    if enhancement.few_shot_example:
        parts.append(enhancement.few_shot_example)

    if enhancement.downstream_contract:
        parts.append(enhancement.downstream_contract)

    if enhancement.self_check:
        parts.append(enhancement.self_check)

    return "\n".join(parts)
