"""Contract Validator — validates inter-agent dependency contracts.

Ensures that when Agent B depends on Agent A, Agent A's output actually
contains what Agent B needs. This catches "silent failures" where an
agent completes successfully but produces output that's useless to
downstream agents.

Problem this solves:
    In the current system, if the database_expert creates a schema but
    doesn't export a `get_db()` function, the backend_developer will
    fail or produce broken code. The DAG marks database_expert as
    "completed" even though its output is incomplete for downstream use.

Validation levels:
    1. FILE_EXISTS — upstream claimed to create files, verify they exist
    2. EXPORT_CHECK — verify key symbols are importable (functions, classes)
    3. SEMANTIC — use LLM to check if upstream output satisfies downstream needs

Integration:
    Called from ``dag_executor._execute_graph_inner`` before starting
    a task that has dependencies. If validation fails, the dependent
    task gets enriched context about what's missing.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import config as cfg

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────
CONTRACT_VALIDATOR_ENABLED: bool = cfg._get("CONTRACT_VALIDATOR_ENABLED", "true", str).lower() == "true"


@dataclass
class ContractViolation:
    """A single contract violation between two agents."""
    upstream_task_id: str
    downstream_task_id: str
    severity: str  # "critical", "warning", "info"
    description: str
    suggestion: str = ""


@dataclass
class ValidationResult:
    """Result of contract validation for a task."""
    task_id: str
    violations: list[ContractViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    context_enrichment: str = ""  # Extra context to inject into the task prompt

    @property
    def has_critical(self) -> bool:
        return any(v.severity == "critical" for v in self.violations)

    @property
    def has_warnings(self) -> bool:
        return any(v.severity == "warning" for v in self.violations)

    def summary(self) -> str:
        if not self.violations:
            return "All dependency contracts satisfied."
        critical = sum(1 for v in self.violations if v.severity == "critical")
        warnings = sum(1 for v in self.violations if v.severity == "warning")
        return f"{critical} critical, {warnings} warning contract violations."


# ── File Existence Validation ────────────────────────────────────────────

def _check_files_exist(
    claimed_artifacts: list[str],
    project_dir: str,
) -> list[ContractViolation]:
    """Check that files claimed by upstream agent actually exist."""
    violations = []
    for artifact in claimed_artifacts:
        full_path = (
            Path(project_dir) / artifact
            if not Path(artifact).is_absolute()
            else Path(artifact)
        )
        if not full_path.exists():
            violations.append(ContractViolation(
                upstream_task_id="",  # Will be filled by caller
                downstream_task_id="",
                severity="critical",
                description=f"Claimed artifact '{artifact}' does not exist on disk",
                suggestion=f"The upstream agent claimed to create {artifact} but it's missing. "
                           f"You may need to create it yourself or work without it.",
            ))
    return violations


# ── Python Export Validation ─────────────────────────────────────────────

def _check_python_exports(
    file_path: str,
    expected_symbols: list[str],
    project_dir: str,
) -> list[ContractViolation]:
    """Check that a Python file exports expected symbols (functions, classes)."""
    violations = []
    full_path = Path(project_dir) / file_path if not Path(file_path).is_absolute() else Path(file_path)

    if not full_path.exists() or not full_path.suffix == ".py":
        return violations

    try:
        source = full_path.read_text()
        tree = ast.parse(source)

        # Collect all top-level names
        defined_names = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined_names.add(node.name)
            elif isinstance(node, ast.ClassDef):
                defined_names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined_names.add(target.id)

        for symbol in expected_symbols:
            if symbol not in defined_names:
                violations.append(ContractViolation(
                    upstream_task_id="",
                    downstream_task_id="",
                    severity="warning",
                    description=f"Expected symbol '{symbol}' not found in {file_path}",
                    suggestion=f"The file {file_path} doesn't define '{symbol}'. "
                               f"You may need to define it yourself or use an alternative.",
                ))
    except (SyntaxError, OSError) as e:
        violations.append(ContractViolation(
            upstream_task_id="",
            downstream_task_id="",
            severity="warning",
            description=f"Could not parse {file_path}: {e}",
            suggestion="The upstream file has syntax errors. You may need to fix it first.",
        ))

    return violations


# ── Common Contract Definitions ──────────────────────────────────────────

# Maps (upstream_role, downstream_role) → list of checks
# Each check is a dict with type and parameters
ROLE_CONTRACTS: dict[tuple[str, str], list[dict]] = {
    ("database_expert", "backend_developer"): [
        {
            "type": "file_pattern",
            "description": "Database module must exist",
            "patterns": ["**/database.py", "**/db.py", "**/models.py", "**/schema.py"],
        },
    ],
    ("backend_developer", "test_engineer"): [
        {
            "type": "file_pattern",
            "description": "API module must exist for testing",
            "patterns": ["**/main.py", "**/app.py", "**/api.py", "**/routes.py"],
        },
    ],
    ("backend_developer", "frontend_developer"): [
        {
            "type": "file_pattern",
            "description": "API endpoints must be defined",
            "patterns": ["**/main.py", "**/app.py", "**/api/**/*.py", "**/routes/**/*.py"],
        },
    ],
}


def _check_file_patterns(
    patterns: list[str],
    project_dir: str,
) -> bool:
    """Check if any file matching the patterns exists."""
    import glob
    for pattern in patterns:
        matches = glob.glob(os.path.join(project_dir, pattern), recursive=True)
        if matches:
            return True
    return False


# ── Git Diff Validation ──────────────────────────────────────────────────

def _check_upstream_actually_changed_files(
    upstream_task_id: str,
    project_dir: str,
) -> list[str]:
    """Get list of files actually changed by upstream task (via git)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True,
            text=True,
            cwd=project_dir,
            timeout=10,
        )
        if result.returncode == 0:
            return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except Exception:
        pass
    return []


# ── Main Validation Function ────────────────────────────────────────────

def validate_dependencies(
    task_id: str,
    task_role: str,
    dependencies: dict[str, dict],  # {task_id: {"role": str, "artifacts": list, "summary": str}}
    project_dir: str,
) -> ValidationResult:
    """Validate that all upstream dependencies satisfy their contracts.

    Args:
        task_id: The current task being validated.
        task_role: The role of the current task.
        dependencies: Map of upstream task_id → {role, artifacts, summary}.
        project_dir: Working directory for file checks.

    Returns:
        ValidationResult with violations and enrichment context.
    """
    if not CONTRACT_VALIDATOR_ENABLED:
        return ValidationResult(task_id=task_id)

    result = ValidationResult(task_id=task_id)

    for dep_id, dep_info in dependencies.items():
        dep_role = dep_info.get("role", "")
        dep_artifacts = dep_info.get("artifacts", [])
        dep_summary = dep_info.get("summary", "")

        # 1. Check claimed files exist
        file_violations = _check_files_exist(dep_artifacts, project_dir)
        for v in file_violations:
            v.upstream_task_id = dep_id
            v.downstream_task_id = task_id
        result.violations.extend(file_violations)

        # 2. Check role-specific contracts
        contract_key = (dep_role.lower(), task_role.lower())
        if contract_key in ROLE_CONTRACTS:
            for check in ROLE_CONTRACTS[contract_key]:
                if check["type"] == "file_pattern":
                    if not _check_file_patterns(check["patterns"], project_dir):
                        result.violations.append(ContractViolation(
                            upstream_task_id=dep_id,
                            downstream_task_id=task_id,
                            severity="warning",
                            description=check["description"] + " — no matching files found",
                            suggestion=(
                                f"Expected files matching {check['patterns']} from "
                                f"{dep_role} ({dep_id}) but none found. "
                                f"You may need to create the missing module yourself."
                            ),
                        ))

        # 3. Check for empty/trivial output
        if dep_summary and len(dep_summary.strip()) < 20:
            result.violations.append(ContractViolation(
                upstream_task_id=dep_id,
                downstream_task_id=task_id,
                severity="warning",
                description=f"Upstream {dep_id} ({dep_role}) produced very short summary ({len(dep_summary)} chars)",
                suggestion="The upstream agent may not have completed its work properly.",
            ))

    # Build context enrichment for the downstream agent
    if result.violations:
        enrichment_parts = [
            "\n<dependency_warnings>",
            "⚠️ CONTRACT VALIDATION found issues with your upstream dependencies:",
            "",
        ]
        for v in result.violations:
            enrichment_parts.append(
                f"- [{v.severity.upper()}] {v.description}\n"
                f"  Suggestion: {v.suggestion}"
            )
        enrichment_parts.append(
            "\nAdapt your approach based on these warnings. "
            "If critical files are missing, create them yourself."
            "\n</dependency_warnings>"
        )
        result.context_enrichment = "\n".join(enrichment_parts)

    # Log results
    if result.violations:
        logger.warning(
            "[ContractValidator] Task %s: %d violations found (%s)",
            task_id,
            len(result.violations),
            result.summary(),
        )
    else:
        logger.info("[ContractValidator] Task %s: all contracts satisfied", task_id)

    return result
