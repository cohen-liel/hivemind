"""Code Execution Gate — validates agent-produced code before acceptance.

After an agent produces code files, this gate runs lightweight validation
checks to catch obvious errors BEFORE the output is committed. If errors
are found, they are fed back to the agent for a fix turn.

Validation levels (configurable):
    1. SYNTAX  — ``ast.parse()`` for Python, basic checks for other languages
    2. IMPORT  — Verify all imports resolve (no missing dependencies)
    3. TEST    — Run ``pytest`` on test files with a short timeout
    4. LINT    — Run ``ruff`` or ``flake8`` for style/error checks

Each level is independent and can be enabled/disabled via config.

Integration:
    Called from ``dag_executor._run_single_task`` after Reflexion but before
    the output is committed. Feeds validation errors back through the
    existing Reflexion fix mechanism.

Design principles:
    - Non-blocking: validation runs with strict timeouts
    - Safe: code runs in subprocess with resource limits
    - Informative: error messages are structured for LLM consumption
    - Graceful: validation failures don't crash the DAG — they add issues
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import config as cfg

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────
CODE_GATE_ENABLED: bool = cfg._get("CODE_GATE_ENABLED", "true", str).lower() == "true"
CODE_GATE_SYNTAX: bool = cfg._get("CODE_GATE_SYNTAX", "true", str).lower() == "true"
CODE_GATE_IMPORTS: bool = cfg._get("CODE_GATE_IMPORTS", "true", str).lower() == "true"
CODE_GATE_TESTS: bool = cfg._get("CODE_GATE_TESTS", "true", str).lower() == "true"
CODE_GATE_LINT: bool = cfg._get("CODE_GATE_LINT", "false", str).lower() == "true"
CODE_GATE_TEST_TIMEOUT: int = cfg._get("CODE_GATE_TEST_TIMEOUT", "30", int)
CODE_GATE_MAX_ERRORS: int = cfg._get("CODE_GATE_MAX_ERRORS", "10", int)


@dataclass
class ValidationError:
    """A single validation error found in agent code."""

    file: str
    line: int | None
    level: str  # "syntax", "import", "test", "lint"
    message: str
    severity: str = "error"  # "error", "warning"

    def to_prompt_str(self) -> str:
        loc = f":{self.line}" if self.line else ""
        return f"[{self.level.upper()}] {self.file}{loc} — {self.message}"


@dataclass
class ValidationResult:
    """Aggregated result of all validation checks."""

    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)
    files_checked: int = 0
    tests_run: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    elapsed_seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def to_prompt(self) -> str:
        """Format validation results for LLM consumption."""
        if self.passed:
            summary = f"All {self.files_checked} files passed validation."
            if self.tests_run > 0:
                summary += f" {self.tests_passed}/{self.tests_run} tests passed."
            return summary

        lines = [
            f"## CODE VALIDATION FAILED\n",
            f"Checked {self.files_checked} files. "
            f"Found {len(self.errors)} error(s) and {len(self.warnings)} warning(s).\n",
        ]

        if self.tests_run > 0:
            lines.append(
                f"Tests: {self.tests_passed}/{self.tests_run} passed, "
                f"{self.tests_failed} failed.\n"
            )

        lines.append("**Errors that MUST be fixed:**\n")
        for err in self.errors[:CODE_GATE_MAX_ERRORS]:
            lines.append(f"  - {err.to_prompt_str()}")

        if len(self.errors) > CODE_GATE_MAX_ERRORS:
            lines.append(f"  ... and {len(self.errors) - CODE_GATE_MAX_ERRORS} more errors")

        if self.warnings:
            lines.append("\n**Warnings (fix if possible):**\n")
            for warn in self.warnings[:5]:
                lines.append(f"  - {warn.to_prompt_str()}")

        return "\n".join(lines)


def _is_python_file(path: str) -> bool:
    return path.endswith(".py")


def _is_test_file(path: str) -> bool:
    name = Path(path).name
    return name.startswith("test_") or name.endswith("_test.py") or "tests/" in path


# ── Syntax Validation ────────────────────────────────────────────────────


def check_syntax(file_path: str, project_dir: str) -> list[ValidationError]:
    """Check Python syntax using ast.parse(). Zero dependencies."""
    errors = []
    full_path = Path(project_dir) / file_path if not Path(file_path).is_absolute() else Path(file_path)

    if not full_path.exists():
        return errors  # File doesn't exist — already caught by artifact validation

    if not _is_python_file(str(file_path)):
        return errors  # Only check Python files

    try:
        source = full_path.read_text(encoding="utf-8", errors="replace")
        ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        errors.append(
            ValidationError(
                file=file_path,
                line=exc.lineno,
                level="syntax",
                message=f"SyntaxError: {exc.msg}",
            )
        )
    except Exception as exc:
        errors.append(
            ValidationError(
                file=file_path,
                line=None,
                level="syntax",
                message=f"Parse error: {str(exc)[:200]}",
            )
        )

    return errors


# ── Import Validation ────────────────────────────────────────────────────


def check_imports(file_path: str, project_dir: str) -> list[ValidationError]:
    """Check that all imports in a Python file can be resolved.

    Uses ast to extract imports, then checks:
    1. stdlib modules (always available)
    2. installed packages (importlib.util.find_spec)
    3. project-local modules (file exists in project)
    """
    errors = []
    full_path = Path(project_dir) / file_path if not Path(file_path).is_absolute() else Path(file_path)

    if not full_path.exists() or not _is_python_file(str(file_path)):
        return errors

    try:
        source = full_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return errors  # Syntax errors caught separately

    import importlib.util

    project_path = Path(project_dir)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name.split(".")[0]
                if not _can_resolve_module(module_name, project_path):
                    errors.append(
                        ValidationError(
                            file=file_path,
                            line=node.lineno,
                            level="import",
                            message=f"Cannot resolve import '{alias.name}'",
                            severity="warning",  # Could be a runtime-only import
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module_name = node.module.split(".")[0]
                if node.level == 0 and not _can_resolve_module(module_name, project_path):
                    errors.append(
                        ValidationError(
                            file=file_path,
                            line=node.lineno,
                            level="import",
                            message=f"Cannot resolve 'from {node.module} import ...'",
                            severity="warning",
                        )
                    )

    return errors


def _can_resolve_module(module_name: str, project_path: Path) -> bool:
    """Check if a module can be resolved via importlib or project files."""
    import importlib.util

    # Check stdlib + installed packages
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is not None:
            return True
    except (ModuleNotFoundError, ValueError):
        pass

    # Check project-local modules
    # Look for module_name.py or module_name/ directory
    if (project_path / f"{module_name}.py").exists():
        return True
    if (project_path / module_name / "__init__.py").exists():
        return True
    if (project_path / module_name).is_dir():
        return True

    return False


# ── Test Execution ───────────────────────────────────────────────────────


async def run_tests(
    test_files: list[str],
    project_dir: str,
    timeout: int = CODE_GATE_TEST_TIMEOUT,
) -> tuple[list[ValidationError], int, int, int]:
    """Run pytest on test files and return errors.

    Returns: (errors, tests_run, tests_passed, tests_failed)
    """
    errors = []
    if not test_files:
        return errors, 0, 0, 0

    # Check if pytest is available
    pytest_path = None
    for candidate in ["pytest", "python3 -m pytest", f"{sys.executable} -m pytest"]:
        try:
            result = subprocess.run(
                candidate.split(),
                capture_output=True,
                timeout=5,
                cwd=project_dir,
            )
            if result.returncode in (0, 1, 2, 5):  # 5 = no tests collected
                pytest_path = candidate
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    if pytest_path is None:
        logger.warning("[CodeGate] pytest not available, skipping test execution")
        return errors, 0, 0, 0

    # Run pytest with JSON output
    cmd = pytest_path.split() + [
        "--tb=short",
        "--no-header",
        "-q",
        "--timeout=10",  # Per-test timeout
    ] + test_files

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        errors.append(
            ValidationError(
                file="tests",
                line=None,
                level="test",
                message=f"Test execution timed out after {timeout}s",
            )
        )
        return errors, 0, 0, 0
    except Exception as exc:
        logger.warning("[CodeGate] Test execution failed: %s", exc)
        return errors, 0, 0, 0

    output = stdout.decode("utf-8", errors="replace")
    err_output = stderr.decode("utf-8", errors="replace")

    # Parse pytest output
    tests_run = 0
    tests_passed = 0
    tests_failed = 0

    for line in output.split("\n"):
        line = line.strip()
        if "passed" in line or "failed" in line or "error" in line:
            # Parse summary line like "3 passed, 1 failed"
            import re

            passed_match = re.search(r"(\d+) passed", line)
            failed_match = re.search(r"(\d+) failed", line)
            error_match = re.search(r"(\d+) error", line)

            if passed_match:
                tests_passed = int(passed_match.group(1))
            if failed_match:
                tests_failed = int(failed_match.group(1))
            if error_match:
                tests_failed += int(error_match.group(1))

            tests_run = tests_passed + tests_failed

        elif line.startswith("FAILED"):
            # Parse individual failure lines
            errors.append(
                ValidationError(
                    file=line.split("::")[0].replace("FAILED ", "") if "::" in line else "test",
                    line=None,
                    level="test",
                    message=line[:200],
                )
            )

    # If pytest returned non-zero but we didn't parse failures, add generic error
    if proc.returncode not in (0, 5) and not errors:
        error_text = (err_output or output)[:500]
        errors.append(
            ValidationError(
                file="tests",
                line=None,
                level="test",
                message=f"pytest exited with code {proc.returncode}: {error_text}",
            )
        )

    return errors, tests_run, tests_passed, tests_failed


# ── Lint Check ───────────────────────────────────────────────────────────


async def run_lint(
    files: list[str],
    project_dir: str,
) -> list[ValidationError]:
    """Run ruff or flake8 on Python files for quick lint checks."""
    errors = []
    if not files:
        return errors

    # Try ruff first (faster), then flake8
    linter = None
    for candidate in ["ruff check", "flake8"]:
        try:
            result = subprocess.run(
                candidate.split() + ["--version"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                linter = candidate
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    if linter is None:
        logger.debug("[CodeGate] No linter available (ruff/flake8), skipping lint")
        return errors

    cmd = linter.split() + [
        "--select=E,F",  # Only errors and pyflakes (skip style)
        "--max-line-length=120",
    ] + files

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning("[CodeGate] Lint execution failed: %s", exc)
        return errors

    for line in stdout.decode("utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line or line.startswith("Found"):
            continue
        # Parse "file.py:10:5: E302 expected 2 blank lines"
        parts = line.split(":", 3)
        if len(parts) >= 4:
            errors.append(
                ValidationError(
                    file=parts[0],
                    line=int(parts[1]) if parts[1].isdigit() else None,
                    level="lint",
                    message=parts[3].strip()[:200],
                    severity="warning",
                )
            )

    return errors


# ── Main Gate Function ───────────────────────────────────────────────────


async def validate_code(
    artifacts: list[str],
    project_dir: str,
) -> ValidationResult:
    """Run all enabled validation checks on agent-produced code.

    Args:
        artifacts: List of file paths the agent claims to have modified.
        project_dir: The project working directory.

    Returns:
        ValidationResult with all errors, warnings, and test results.
    """
    import time

    t0 = time.monotonic()
    result = ValidationResult()

    if not CODE_GATE_ENABLED:
        return result

    # Filter to Python files that exist
    python_files = []
    test_files = []
    for artifact in artifacts:
        if not _is_python_file(artifact):
            continue
        full_path = (
            Path(project_dir) / artifact
            if not Path(artifact).is_absolute()
            else Path(artifact)
        )
        if not full_path.exists():
            continue
        python_files.append(artifact)
        if _is_test_file(artifact):
            test_files.append(artifact)

    result.files_checked = len(python_files)

    if not python_files:
        result.elapsed_seconds = time.monotonic() - t0
        return result

    # ── Syntax checks (fast, always first) ──
    if CODE_GATE_SYNTAX:
        for f in python_files:
            syntax_errors = check_syntax(f, project_dir)
            result.errors.extend(syntax_errors)

    # If syntax errors found, skip further checks (they'll fail anyway)
    if result.errors:
        result.elapsed_seconds = time.monotonic() - t0
        logger.info(
            "[CodeGate] Syntax check found %d errors in %d files (%.1fs)",
            len(result.errors),
            result.files_checked,
            result.elapsed_seconds,
        )
        return result

    # ── Import checks ──
    if CODE_GATE_IMPORTS:
        for f in python_files:
            import_errors = check_imports(f, project_dir)
            # Import errors are warnings by default
            for err in import_errors:
                if err.severity == "error":
                    result.errors.append(err)
                else:
                    result.warnings.append(err)

    # ── Test execution ──
    if CODE_GATE_TESTS and test_files:
        test_errors, tests_run, tests_passed, tests_failed = await run_tests(
            test_files, project_dir
        )
        result.errors.extend(test_errors)
        result.tests_run = tests_run
        result.tests_passed = tests_passed
        result.tests_failed = tests_failed

    # ── Lint checks ──
    if CODE_GATE_LINT:
        lint_errors = await run_lint(python_files, project_dir)
        result.warnings.extend(lint_errors)

    result.elapsed_seconds = time.monotonic() - t0

    logger.info(
        "[CodeGate] Validation complete: %d files, %d errors, %d warnings, "
        "%d/%d tests passed (%.1fs)",
        result.files_checked,
        len(result.errors),
        len(result.warnings),
        result.tests_passed,
        result.tests_run,
        result.elapsed_seconds,
    )

    return result
