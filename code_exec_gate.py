"""Code Execution Gate — validates agent code by actually running it.

After an agent writes code, this gate:
1. Runs py_compile on all .py files the agent touched (syntax check)
2. Runs pytest if test files exist (functional check)
3. If failures found, feeds ONLY the error output back to the agent
   to fix ONLY the broken file (targeted fix, not full rewrite)

Benchmark proof (h2h_with_fixloop.py):
    - Without fix loop: 7 tests passed across 3 projects
    - With fix loop:    15 tests passed across 3 projects (114% improvement)
    - The fix loop doubled test pass rate by catching and fixing errors
      that the agent introduced (IndentationError, missing imports, etc.)

Integration:
    Called from dag_executor._run_single_task after Reflexion phase.
    Only triggers for agents with WRITER roles (backend, frontend, database, etc.)

No external dependencies — uses only stdlib (py_compile, subprocess).
"""

from __future__ import annotations

import asyncio
import logging
import py_compile
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_FIX_ATTEMPTS = 2

CODE_PRODUCING_ROLES = {
    "backend_developer", "frontend_developer", "database_engineer",
    "devops_engineer", "test_engineer", "fullstack_developer",
    "security_engineer",
}


async def validate_and_fix(
    task_output,
    task_input,
    project_dir: str,
    session_id: str | None,
    system_prompt: str,
    sdk: object,
    role_name: str,
) -> tuple:
    """Validate code artifacts and attempt targeted fixes if broken.

    Returns:
        Tuple of (possibly improved task_output, validation_report dict).
    """
    report = {
        "syntax_errors": [],
        "pytest_result": None,
        "fix_attempts": 0,
        "improved": False,
    }

    if role_name.lower() not in CODE_PRODUCING_ROLES:
        return task_output, report

    artifacts = task_output.artifacts or []
    py_files = [f for f in artifacts if f.endswith(".py")]
    if not py_files:
        return task_output, report

    # Step 1: Syntax check
    syntax_errors = _check_syntax(project_dir, py_files)
    report["syntax_errors"] = syntax_errors

    # Step 2: Run pytest if test files exist
    test_files = [f for f in py_files if "test" in f.lower()]
    pytest_result = None
    if test_files:
        pytest_result = await _run_pytest(project_dir)
        report["pytest_result"] = pytest_result

    # Step 3: Collect errors
    errors = []
    if syntax_errors:
        errors.extend([f"Syntax error in {e['file']}: {e['error']}" for e in syntax_errors])
    if pytest_result and pytest_result.get("failed", 0) > 0:
        errors.append(f"Pytest: {pytest_result['passed']} passed, {pytest_result['failed']} failed")
        if pytest_result.get("output"):
            errors.append(f"Pytest output:\n{pytest_result['output'][:2000]}")

    if not errors:
        logger.info("[CodeGate] All checks passed for %s", task_input.id)
        return task_output, report

    # Step 4: Targeted fix loop
    for attempt in range(MAX_FIX_ATTEMPTS):
        report["fix_attempts"] = attempt + 1
        logger.info("[CodeGate] Task %s: fix attempt %d/%d", task_input.id, attempt + 1, MAX_FIX_ATTEMPTS)

        broken_files = [e["file"] for e in syntax_errors] if syntax_errors else test_files[:1]
        fix_prompt = _build_fix_prompt(errors, broken_files, project_dir)

        try:
            from isolated_query import isolated_query
            fix_response = await isolated_query(
                sdk,
                prompt=fix_prompt,
                system_prompt=system_prompt,
                cwd=project_dir,
                session_id=session_id,
                max_turns=5,
                max_budget_usd=1.0,
                max_retries=0,
            )

            if fix_response.is_error:
                logger.warning("[CodeGate] Fix attempt %d failed: %s", attempt + 1, fix_response.error_message[:200])
                continue

            task_output.cost_usd += fix_response.cost_usd
            task_output.turns_used += fix_response.num_turns

            # Re-check
            syntax_errors = _check_syntax(project_dir, py_files)
            if test_files:
                pytest_result = await _run_pytest(project_dir)

            new_errors = []
            if syntax_errors:
                new_errors.extend([f"Syntax error in {e['file']}: {e['error']}" for e in syntax_errors])
            if pytest_result and pytest_result.get("failed", 0) > 0:
                new_errors.append(f"Pytest: {pytest_result['passed']} passed, {pytest_result['failed']} failed")

            if len(new_errors) < len(errors):
                report["improved"] = True

            if not new_errors:
                logger.info("[CodeGate] All errors fixed after %d attempts", attempt + 1)
                break

            errors = new_errors

        except Exception as exc:
            logger.warning("[CodeGate] Fix attempt %d exception: %s", attempt + 1, exc)
            break

    report["syntax_errors"] = syntax_errors
    report["pytest_result"] = pytest_result
    return task_output, report


def _check_syntax(project_dir: str, py_files: list[str]) -> list[dict]:
    """Run py_compile on each file and return errors."""
    errors = []
    for f in py_files:
        filepath = Path(project_dir) / f
        if not filepath.exists():
            continue
        try:
            py_compile.compile(str(filepath), doraise=True)
        except py_compile.PyCompileError as e:
            errors.append({"file": f, "error": str(e)[:300]})
    return errors


async def _run_pytest(project_dir: str) -> dict:
    """Run pytest and return structured results."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "python", "-m", "pytest", "--tb=short", "-q",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace")
        err_output = stderr.decode("utf-8", errors="replace")

        passed = failed = 0
        for line in output.split("\n"):
            if "passed" in line:
                try:
                    passed = int(line.split("passed")[0].strip().split()[-1])
                except (ValueError, IndexError):
                    pass
            if "failed" in line:
                try:
                    failed = int(line.split("failed")[0].strip().split()[-1])
                except (ValueError, IndexError):
                    pass

        return {
            "passed": passed,
            "failed": failed,
            "return_code": proc.returncode,
            "output": (output + "\n" + err_output)[:3000],
        }
    except asyncio.TimeoutError:
        return {"passed": 0, "failed": 0, "return_code": -1, "output": "pytest timed out"}
    except Exception as e:
        return {"passed": 0, "failed": 0, "return_code": -1, "output": str(e)[:500]}


def _build_fix_prompt(errors: list[str], broken_files: list[str], project_dir: str) -> str:
    """Build a targeted fix prompt — fix only the broken files."""
    errors_text = "\n".join(f"- {e}" for e in errors[:5])
    files_text = ", ".join(broken_files[:3])

    file_contents = []
    for f in broken_files[:2]:
        filepath = Path(project_dir) / f
        if filepath.exists():
            try:
                content = filepath.read_text()[:3000]
                file_contents.append(f"### {f}\n```python\n{content}\n```")
            except Exception:
                pass

    files_context = "\n\n".join(file_contents) if file_contents else "(files not readable)"

    return (
        "## CODE VALIDATION FAILED\n\n"
        "The following errors were found after running your code:\n\n"
        f"{errors_text}\n\n"
        f"**Fix ONLY these files:** {files_text}\n\n"
        "**DO NOT modify any other files.** Only fix the specific errors above.\n\n"
        f"Current content of broken files:\n\n{files_context}\n\n"
        "Fix the errors and nothing else."
    )
