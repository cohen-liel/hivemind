#!/usr/bin/env python3
"""A/B Benchmark Runner — tests each improvement variant against the baseline.

Runs the same 3 projects with different configurations and collects:
- Test pass rate
- Token usage
- Time
- Code quality score (via LLM review)

Variants:
- baseline: Original prompts, no changes
- enhanced_prompts: Stronger code quality requirements in prompts
- with_memory: Inject cross-project memory lessons into context
- with_review: Add a reviewer agent pass after each task

Usage:
    python3 benchmarks/ab_benchmark.py --variant enhanced_prompts
    python3 benchmarks/ab_benchmark.py --variant with_memory
    python3 benchmarks/ab_benchmark.py --variant with_review
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Ensure hivemind root is on path
HIVEMIND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HIVEMIND_ROOT))
sys.path.insert(0, str(HIVEMIND_ROOT / "benchmarks"))

# ── Monkey-patch isolated_query BEFORE importing dag_executor ──
import isolated_query_openai

sys.modules["isolated_query"] = isolated_query_openai

from code_quality_scorer import score_project

from contracts import AgentRole, TaskGraph, TaskInput
from dag_executor import ExecutionResult, execute_graph
from prompts import PROMPT_REGISTRY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("ab_benchmark")


# ── Test Project Definitions (same as baseline) ──────────────────────────

PROJECTS = {
    "todo_api": {
        "description": "Build a Python FastAPI REST API for todo management with SQLite",
        "vision": "Create a production-quality REST API for managing todo items with full CRUD operations, SQLite persistence, and comprehensive test coverage",
        "epics": [
            "Set up FastAPI project with SQLite database",
            "Implement CRUD endpoints for todos",
            "Write comprehensive pytest test suite",
        ],
        "tasks": [
            TaskInput(
                id="task_001",
                role=AgentRole.BACKEND_DEVELOPER,
                goal=(
                    "Create a FastAPI REST API for todo management. Requirements:\n"
                    "1. Create main.py with FastAPI app\n"
                    "2. Create database.py with SQLite setup using sqlite3 (NOT SQLAlchemy)\n"
                    "3. Create models.py with Pydantic models for Todo (id, title, description, completed, created_at)\n"
                    "4. Implement these endpoints:\n"
                    "   - GET /todos - list all todos\n"
                    "   - POST /todos - create a todo\n"
                    "   - GET /todos/{id} - get a single todo\n"
                    "   - PUT /todos/{id} - update a todo\n"
                    "   - DELETE /todos/{id} - delete a todo\n"
                    "5. Use proper HTTP status codes (201 for create, 404 for not found)\n"
                    "6. Initialize the database table on startup\n"
                    "Make sure the app runs with: uvicorn main:app"
                ),
                depends_on=[],
                acceptance_criteria=[
                    "main.py exists with FastAPI app and all 5 endpoints",
                    "database.py exists with SQLite setup",
                    "models.py exists with Pydantic models",
                    "App starts without errors",
                ],
            ),
            TaskInput(
                id="task_002",
                role=AgentRole.TEST_ENGINEER,
                goal=(
                    "Write comprehensive pytest tests for the todo API. Requirements:\n"
                    "1. Create test_app.py with pytest tests using FastAPI TestClient\n"
                    "2. Test ALL endpoints:\n"
                    "   - Test creating a todo (POST /todos) - verify 201 status and response body\n"
                    "   - Test listing todos (GET /todos) - verify returns list\n"
                    "   - Test getting a single todo (GET /todos/{id}) - verify correct todo returned\n"
                    "   - Test updating a todo (PUT /todos/{id}) - verify changes persisted\n"
                    "   - Test deleting a todo (DELETE /todos/{id}) - verify 200 status\n"
                    "   - Test getting non-existent todo (GET /todos/999) - verify 404\n"
                    "3. Use a fresh test database for each test (use tmp_path or similar)\n"
                    "4. Run the tests with: python -m pytest test_app.py -v\n"
                    "5. Fix any issues found during testing\n"
                    "IMPORTANT: After writing tests, RUN them with pytest and fix any failures."
                ),
                depends_on=["task_001"],
                context_from=["task_001"],
                acceptance_criteria=[
                    "test_app.py exists with tests for all CRUD operations",
                    "Tests actually pass when run with pytest",
                    "At least 6 test functions",
                ],
            ),
        ],
    },
    "calculator_cli": {
        "description": "Build a Python CLI calculator with history and unit tests",
        "vision": "Create a command-line calculator application with operation history, undo functionality, and comprehensive test coverage",
        "epics": [
            "Implement calculator core with basic operations",
            "Add history and undo features",
            "Write comprehensive test suite",
        ],
        "tasks": [
            TaskInput(
                id="task_001",
                role=AgentRole.BACKEND_DEVELOPER,
                goal=(
                    "Create a Python calculator module. Requirements:\n"
                    "1. Create calculator.py with a Calculator class that supports:\n"
                    "   - add(a, b) -> float\n"
                    "   - subtract(a, b) -> float\n"
                    "   - multiply(a, b) -> float\n"
                    "   - divide(a, b) -> float (raise ValueError on division by zero)\n"
                    "   - power(base, exp) -> float\n"
                    "   - sqrt(n) -> float (raise ValueError for negative numbers)\n"
                    "   - history property -> list of (operation, result) tuples\n"
                    "   - undo() -> removes last operation from history\n"
                    "   - clear_history() -> clears all history\n"
                    "2. Each operation should be recorded in history\n"
                    "3. The module should be importable: from calculator import Calculator"
                ),
                depends_on=[],
                acceptance_criteria=[
                    "calculator.py exists with Calculator class",
                    "All 6 math operations implemented",
                    "History tracking works",
                    "Undo and clear_history work",
                ],
            ),
            TaskInput(
                id="task_002",
                role=AgentRole.TEST_ENGINEER,
                goal=(
                    "Write comprehensive pytest tests for the calculator module. Requirements:\n"
                    "1. Create test_calculator.py with pytest tests\n"
                    "2. Test ALL operations: add, subtract, multiply, divide, power, sqrt\n"
                    "3. Test edge cases:\n"
                    "   - Division by zero raises ValueError\n"
                    "   - Square root of negative number raises ValueError\n"
                    "   - Large numbers work correctly\n"
                    "   - Floating point operations are accurate\n"
                    "4. Test history:\n"
                    "   - Operations are recorded in history\n"
                    "   - Undo removes last operation\n"
                    "   - Clear history empties the list\n"
                    "5. Run tests with: python -m pytest test_calculator.py -v\n"
                    "IMPORTANT: After writing tests, RUN them with pytest and fix any failures."
                ),
                depends_on=["task_001"],
                context_from=["task_001"],
                acceptance_criteria=[
                    "test_calculator.py exists with comprehensive tests",
                    "Tests pass when run with pytest",
                    "At least 10 test functions covering all operations and edge cases",
                ],
            ),
        ],
    },
    "url_shortener": {
        "description": "Build a URL shortener API with FastAPI and SQLite",
        "vision": "Create a URL shortener service with short code generation, redirect, analytics, and tests",
        "epics": [
            "Set up FastAPI with SQLite storage",
            "Implement URL shortening and redirect",
            "Add click analytics",
            "Write comprehensive tests",
        ],
        "tasks": [
            TaskInput(
                id="task_001",
                role=AgentRole.BACKEND_DEVELOPER,
                goal=(
                    "Create a URL shortener API with FastAPI. Requirements:\n"
                    "1. Create main.py with FastAPI app\n"
                    "2. Create database.py with SQLite setup (using sqlite3, NOT SQLAlchemy)\n"
                    "3. Create models.py with Pydantic models\n"
                    "4. Implement endpoints:\n"
                    "   - POST /shorten - accepts {url: string}, returns {short_code: string, short_url: string}\n"
                    "   - GET /{short_code} - redirects to original URL (307 redirect)\n"
                    "   - GET /stats/{short_code} - returns {url, short_code, clicks, created_at}\n"
                    "   - DELETE /{short_code} - deletes a shortened URL\n"
                    "5. Generate random 6-character alphanumeric short codes\n"
                    "6. Track click count for each URL\n"
                    "7. Return 404 for non-existent short codes\n"
                    "Make sure the app runs with: uvicorn main:app"
                ),
                depends_on=[],
                acceptance_criteria=[
                    "main.py with all 4 endpoints",
                    "database.py with SQLite",
                    "Short code generation works",
                    "Click tracking increments on redirect",
                ],
            ),
            TaskInput(
                id="task_002",
                role=AgentRole.TEST_ENGINEER,
                goal=(
                    "Write comprehensive pytest tests for the URL shortener. Requirements:\n"
                    "1. Create test_app.py with pytest tests using FastAPI TestClient\n"
                    "2. Test ALL endpoints:\n"
                    "   - Test shortening a URL (POST /shorten)\n"
                    "   - Test redirect (GET /{short_code}) - verify 307 and Location header\n"
                    "   - Test stats endpoint (GET /stats/{short_code})\n"
                    "   - Test click count increments after redirect\n"
                    "   - Test deleting a URL (DELETE /{short_code})\n"
                    "   - Test 404 for non-existent short code\n"
                    "   - Test shortening invalid URL\n"
                    "3. Use a fresh test database for each test\n"
                    "4. Run tests with: python -m pytest test_app.py -v\n"
                    "IMPORTANT: After writing tests, RUN them with pytest and fix any failures."
                ),
                depends_on=["task_001"],
                context_from=["task_001"],
                acceptance_criteria=[
                    "test_app.py with tests for all endpoints",
                    "Tests pass with pytest",
                    "At least 7 test functions",
                ],
            ),
        ],
    },
}


# ── Variant Configurations ───────────────────────────────────────────────


def get_prompts_for_variant(variant: str) -> dict[str, str]:
    """Get the prompt registry for a given variant."""
    if variant == "enhanced_prompts":
        from enhanced_prompts import ENHANCED_PROMPT_REGISTRY

        return ENHANCED_PROMPT_REGISTRY
    elif variant == "with_memory":
        # Use original prompts but inject memory context into task goals
        return PROMPT_REGISTRY
    elif variant == "with_review":
        return PROMPT_REGISTRY
    else:
        return PROMPT_REGISTRY


def get_tasks_for_variant(variant: str, project_name: str) -> list[TaskInput]:
    """Get tasks, potentially modified for the variant."""
    base_tasks = list(PROJECTS[project_name]["tasks"])  # shallow copy

    if variant == "with_memory":
        # Inject cross-project memory lessons into task goals
        memory_context = _get_memory_lessons()
        enhanced_tasks = []
        for task in base_tasks:
            enhanced_goal = (
                f"{task.goal}\n\n"
                f"<cross_project_lessons>\n"
                f"The following lessons were learned from previous similar projects. "
                f"Apply them where relevant:\n{memory_context}\n"
                f"</cross_project_lessons>"
            )
            enhanced_tasks.append(
                TaskInput(
                    id=task.id,
                    role=task.role,
                    goal=enhanced_goal,
                    depends_on=task.depends_on,
                    context_from=getattr(task, "context_from", []),
                    acceptance_criteria=task.acceptance_criteria,
                )
            )
        return enhanced_tasks

    elif variant == "with_review":
        # Add a reviewer task after the test engineer
        review_task = TaskInput(
            id="task_003",
            role=AgentRole.REVIEWER,
            goal=(
                "Review all code created by previous tasks. Requirements:\n"
                "1. Read all .py files in the project\n"
                "2. Check for code quality issues:\n"
                "   - Missing type hints\n"
                "   - Missing error handling\n"
                "   - Missing docstrings\n"
                "   - Security issues (SQL injection, etc.)\n"
                "   - Code duplication\n"
                "3. Fix any MUST-FIX issues directly in the code\n"
                "4. Run pytest after fixes to ensure nothing broke\n"
                "5. Write review findings to .hivemind/REVIEW.md\n"
                "IMPORTANT: Actually FIX critical issues, don't just report them."
            ),
            depends_on=["task_002"],
            context_from=["task_001", "task_002"],
            acceptance_criteria=[
                "All critical code quality issues fixed",
                "Tests still pass after fixes",
                "Review report written",
            ],
        )
        return [*base_tasks, review_task]

    return base_tasks


def _get_memory_lessons() -> str:
    """Get simulated cross-project memory lessons."""
    return """
1. LESSON (from todo_api_v1): Always use parameterized SQL queries to prevent SQL injection. Never use f-strings for SQL.
2. LESSON (from auth_service): Use `logging.getLogger(__name__)` instead of print() for all output. Configure logging at app startup.
3. LESSON (from payment_api): Always define custom exception classes (e.g., ResourceNotFoundError) instead of using generic ValueError/RuntimeError.
4. LESSON (from user_service): Add type hints to ALL function signatures. This catches bugs early and improves IDE support.
5. LESSON (from analytics_api): Use constants for magic numbers. Define SHORT_CODE_LENGTH = 6 instead of hardcoding 6 everywhere.
6. LESSON (from file_service): Always validate input at API boundaries using Pydantic validators. Don't trust any external input.
7. LESSON (from search_api): Write docstrings for all public functions using Google-style format (Args, Returns, Raises).
8. LESSON (from notification_service): Use context managers (with statements) for all resource management (files, DB connections).
9. LESSON (from report_api): Separate database operations into their own functions. Don't mix SQL with business logic.
10. LESSON (from chat_api): In tests, always use isolated test databases (tmp_path) and clean up after each test.
"""


# ── Benchmark Runner ─────────────────────────────────────────────────────


class DummySDK:
    pass


def _run_pytest(project_dir: str) -> dict:
    """Run pytest in the project directory and parse results."""
    test_files = [f for f in os.listdir(project_dir) if f.startswith("test_") and f.endswith(".py")]
    if not test_files:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "output": "No test files found"}

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short", "--no-header", *test_files],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout + "\n" + result.stderr
        passed = failed = errors = 0
        for line in output.split("\n"):
            line = line.strip()
            if "passed" in line or "failed" in line or "error" in line:
                m_passed = re.search(r"(\d+) passed", line)
                m_failed = re.search(r"(\d+) failed", line)
                m_errors = re.search(r"(\d+) error", line)
                if m_passed:
                    passed = int(m_passed.group(1))
                if m_failed:
                    failed = int(m_failed.group(1))
                if m_errors:
                    errors = int(m_errors.group(1))
        return {
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "total": passed + failed + errors,
            "output": output[-2000:],
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "output": "pytest timed out"}
    except Exception as e:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "output": f"Error: {e}"}


async def run_single_benchmark(
    project_name: str,
    variant: str,
    output_dir: str,
) -> dict:
    """Run a single project benchmark with a specific variant."""
    project_def = PROJECTS[project_name]
    project_dir = os.path.join(output_dir, f"{variant}_{project_name}")
    os.makedirs(project_dir, exist_ok=True)

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "benchmark@hivemind.test"],
        cwd=project_dir,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Benchmark"], cwd=project_dir, capture_output=True
    )
    os.makedirs(os.path.join(project_dir, ".hivemind"), exist_ok=True)

    # Get variant-specific config
    prompts = get_prompts_for_variant(variant)
    tasks = get_tasks_for_variant(variant, project_name)

    graph = TaskGraph(
        project_id=f"bench_{variant}_{project_name}",
        user_message=project_def["description"],
        vision=project_def["vision"],
        epic_breakdown=project_def["epics"],
        tasks=tasks,
    )

    logger.info(f"\n{'=' * 60}")
    logger.info(f"A/B BENCHMARK: {project_name} ({variant})")
    logger.info(f"Project dir: {project_dir}")
    logger.info(f"Tasks: {len(graph.tasks)}")
    logger.info(f"{'=' * 60}\n")

    t0 = time.monotonic()

    try:
        result: ExecutionResult = await execute_graph(
            graph=graph,
            project_dir=project_dir,
            specialist_prompts=prompts,
            sdk_client=DummySDK(),
            max_budget_usd=10.0,
            max_concurrent_tasks=1,
        )
    except Exception as e:
        logger.error(f"DAG execution failed: {e}", exc_info=True)
        return {
            "project": project_name,
            "variant": variant,
            "error": str(e),
            "project_dir": project_dir,
        }

    elapsed = time.monotonic() - t0

    # Run pytest
    test_results = _run_pytest(project_dir)

    # Score code quality
    logger.info(f"Scoring code quality for {project_name} ({variant})...")
    quality = score_project(project_dir)

    # Collect files
    files_created = []
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for f in files:
            if not f.startswith("."):
                rel = os.path.relpath(os.path.join(root, f), project_dir)
                files_created.append(rel)

    metrics = {
        "project": project_name,
        "variant": variant,
        "project_dir": project_dir,
        "elapsed_seconds": round(elapsed, 1),
        "total_tokens": result.total_tokens,
        "total_input_tokens": result.total_input_tokens,
        "total_output_tokens": result.total_output_tokens,
        "tasks_total": len(graph.tasks),
        "tasks_succeeded": result.success_count,
        "tasks_failed": result.failure_count,
        "remediations": result.remediation_count,
        "files_created": files_created,
        "files_count": len(files_created),
        "test_results": test_results,
        "tests_passed": test_results.get("passed", 0),
        "tests_failed": test_results.get("failed", 0),
        "tests_total": test_results.get("total", 0),
        "code_quality": quality,
        "code_quality_score": quality.get("overall_score", 0),
    }

    return metrics


async def run_all(variant: str) -> list[dict]:
    """Run all projects for a variant."""
    output_dir = os.path.join(
        str(HIVEMIND_ROOT), "benchmarks", "results", f"ab_{variant}_{int(time.time())}"
    )
    os.makedirs(output_dir, exist_ok=True)

    results = []
    for project_name in PROJECTS:
        logger.info(f"\n{'#' * 60}")
        logger.info(f"# A/B Benchmark: {project_name} ({variant})")
        logger.info(f"{'#' * 60}\n")

        metrics = await run_single_benchmark(project_name, variant, output_dir)
        results.append(metrics)

        # Save individual result
        result_file = os.path.join(output_dir, f"{variant}_{project_name}_results.json")
        with open(result_file, "w") as f:
            json.dump(metrics, f, indent=2, default=str)

        logger.info(f"\n{'=' * 60}")
        logger.info(f"RESULT: {project_name} ({variant})")
        logger.info(f"  Tasks: {metrics.get('tasks_succeeded', 0)}/{metrics.get('tasks_total', 0)}")
        logger.info(
            f"  Tests: {metrics.get('tests_passed', 0)}/{metrics.get('tests_total', 0)} passed"
        )
        logger.info(f"  Tokens: {metrics.get('total_tokens', 0)}")
        logger.info(f"  Time: {metrics.get('elapsed_seconds', 0)}s")
        logger.info(f"  Code Quality: {metrics.get('code_quality_score', 'N/A')}/10")
        logger.info(f"{'=' * 60}\n")

    # Save combined
    combined_file = os.path.join(output_dir, f"{variant}_combined_results.json")
    with open(combined_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Print summary
    print_summary(results)

    return results


def print_summary(results: list[dict]):
    """Print formatted summary."""
    print(f"\n{'=' * 90}")
    print(f"{'A/B BENCHMARK RESULTS':^90}")
    print(f"{'=' * 90}")
    print(
        f"{'Project':<18} {'Variant':<18} {'Tasks':<8} {'Tests':<12} {'Tokens':<10} {'Time':<8} {'Quality':<8}"
    )
    print("-" * 90)

    total_passed = total_total = total_tokens = 0
    quality_scores = []

    for r in results:
        if "error" in r:
            print(f"{r['project']:<18} {r['variant']:<18} ERROR: {r['error'][:40]}")
            continue

        tasks = f"{r['tasks_succeeded']}/{r['tasks_total']}"
        tests = f"{r['tests_passed']}/{r['tests_total']}"
        tokens = str(r["total_tokens"])
        time_s = f"{r['elapsed_seconds']}s"
        quality = f"{r.get('code_quality_score', 'N/A')}/10"

        print(
            f"{r['project']:<18} {r['variant']:<18} {tasks:<8} {tests:<12} {tokens:<10} {time_s:<8} {quality:<8}"
        )

        total_passed += r.get("tests_passed", 0)
        total_total += r.get("tests_total", 0)
        total_tokens += r.get("total_tokens", 0)
        if r.get("code_quality_score"):
            quality_scores.append(r["code_quality_score"])

    avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0
    print("-" * 90)
    print(
        f"{'TOTAL':<18} {'':<18} {'':<8} {total_passed}/{total_total:<11} {total_tokens:<10} {'':<8} {avg_quality:.1f}/10"
    )
    print(f"{'=' * 90}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A/B Benchmark Runner")
    parser.add_argument(
        "--variant",
        required=True,
        choices=["enhanced_prompts", "with_memory", "with_review"],
        help="Which improvement variant to test",
    )
    parser.add_argument(
        "--project",
        default=None,
        choices=list(PROJECTS.keys()),
        help="Run a single project (default: all)",
    )
    args = parser.parse_args()

    if args.project:
        output_dir = os.path.join(
            str(HIVEMIND_ROOT), "benchmarks", "results", f"ab_{args.variant}_{int(time.time())}"
        )
        os.makedirs(output_dir, exist_ok=True)
        results = [asyncio.run(run_single_benchmark(args.project, args.variant, output_dir))]
        print_summary(results)
    else:
        results = asyncio.run(run_all(args.variant))
