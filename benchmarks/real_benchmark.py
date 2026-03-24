#!/usr/bin/env python3
"""Real HiveMind Benchmark — runs actual DAG execution with gpt-4.1-mini.

This benchmark:
1. Creates a fresh project directory
2. Constructs a TaskGraph (same as PM agent would produce)
3. Runs execute_graph() with the OpenAI-based isolated_query
4. Agents actually create files, run commands, write tests
5. Runs pytest at the end to measure real test pass rate
6. Records all metrics: tokens, time, test results, files created

Usage:
    python3 benchmarks/real_benchmark.py [--variant baseline|chromadb|llmlingua|review_loop]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Ensure hivemind root is on path
HIVEMIND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HIVEMIND_ROOT))

# ── Monkey-patch isolated_query BEFORE importing dag_executor ──
import isolated_query_openai

sys.modules["isolated_query"] = isolated_query_openai

from contracts import AgentRole, TaskGraph, TaskInput
from dag_executor_langgraph import ExecutionResult, execute_graph
from prompts import PROMPT_REGISTRY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("benchmark")

# ── Test Project Definitions ─────────────────────────────────────────────

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
                    "   - Test redirect works (GET /{short_code}) - check 307 status\n"
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


# ── Benchmark Runner ─────────────────────────────────────────────────────


class DummySDK:
    """Dummy SDK client — isolated_query_openai ignores it anyway."""

    pass


async def run_benchmark(
    project_name: str,
    variant: str = "baseline",
    output_dir: str | None = None,
) -> dict:
    """Run a single benchmark project and return metrics."""
    project_def = PROJECTS[project_name]

    # Create fresh project directory
    if output_dir:
        project_dir = os.path.join(output_dir, f"{variant}_{project_name}")
    else:
        project_dir = tempfile.mkdtemp(prefix=f"hivemind_{variant}_{project_name}_")

    os.makedirs(project_dir, exist_ok=True)

    # Initialize git repo (needed by dag_executor for git status checks)
    subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "benchmark@hivemind.test"],
        cwd=project_dir,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Benchmark"],
        cwd=project_dir,
        capture_output=True,
    )
    # Create .hivemind directory for structured notes
    os.makedirs(os.path.join(project_dir, ".hivemind"), exist_ok=True)

    # Build TaskGraph
    graph = TaskGraph(
        project_id=f"bench_{variant}_{project_name}",
        user_message=project_def["description"],
        vision=project_def["vision"],
        epic_breakdown=project_def["epics"],
        tasks=project_def["tasks"],
    )

    logger.info(f"\n{'=' * 60}")
    logger.info(f"BENCHMARK: {project_name} ({variant})")
    logger.info(f"Project dir: {project_dir}")
    logger.info(f"Tasks: {len(graph.tasks)}")
    logger.info(f"{'=' * 60}\n")

    t0 = time.monotonic()

    # Run the DAG executor
    try:
        result: ExecutionResult = await execute_graph(
            graph=graph,
            project_dir=project_dir,
            specialist_prompts=PROMPT_REGISTRY,
            sdk_client=DummySDK(),
            max_budget_usd=10.0,
            max_concurrent_tasks=1,  # Sequential for reproducibility
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

    # ── Post-execution: run pytest to measure actual test results ──
    test_results = _run_pytest(project_dir)

    # ── Collect file metrics ──
    files_created = []
    for root, dirs, files in os.walk(project_dir):
        # Skip hidden dirs and __pycache__
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for f in files:
            if not f.startswith("."):
                rel = os.path.relpath(os.path.join(root, f), project_dir)
                files_created.append(rel)

    # ── Build metrics ──
    metrics = {
        "project": project_name,
        "variant": variant,
        "project_dir": project_dir,
        "elapsed_seconds": round(elapsed, 1),
        "total_tokens": result.total_tokens,
        "total_input_tokens": result.total_input_tokens,
        "total_output_tokens": result.total_output_tokens,
        "total_cost_usd": round(result.total_cost, 4),
        "tasks_total": len(graph.tasks),
        "tasks_succeeded": result.success_count,
        "tasks_failed": result.failure_count,
        "remediations": result.remediation_count,
        "files_created": files_created,
        "files_count": len(files_created),
        "test_results": test_results,
        "tests_passed": test_results.get("passed", 0),
        "tests_failed": test_results.get("failed", 0),
        "tests_errors": test_results.get("errors", 0),
        "tests_total": test_results.get("total", 0),
        "task_outputs": [],
    }

    # Add per-task details
    for output in result.outputs:
        metrics["task_outputs"].append(
            {
                "task_id": output.task_id,
                "status": output.status.value,
                "summary": output.summary[:200],
                "confidence": output.confidence,
                "turns_used": output.turns_used,
                "tokens": output.total_tokens,
                "artifacts": output.artifacts[:10],
            }
        )

    return metrics


def _run_pytest(project_dir: str) -> dict:
    """Run pytest in the project directory and parse results."""
    # Find test files
    test_files = []
    for f in os.listdir(project_dir):
        if f.startswith("test_") and f.endswith(".py"):
            test_files.append(f)

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

        # Parse pytest output
        passed = 0
        failed = 0
        errors = 0

        for line in output.split("\n"):
            line = line.strip()
            # Look for the summary line like "5 passed, 2 failed"
            if "passed" in line or "failed" in line or "error" in line:
                import re

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
            "output": output[-2000:],  # Last 2000 chars
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "output": "pytest timed out"}
    except Exception as e:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "output": f"Error: {e}"}


async def run_all_benchmarks(
    variant: str = "baseline", output_base: str | None = None
) -> list[dict]:
    """Run all benchmark projects and return results."""
    results = []
    output_dir = output_base or os.path.join(
        str(HIVEMIND_ROOT), "benchmarks", "results", f"run_{int(time.time())}"
    )
    os.makedirs(output_dir, exist_ok=True)

    for project_name in PROJECTS:
        logger.info(f"\n{'#' * 60}")
        logger.info(f"# Starting benchmark: {project_name} ({variant})")
        logger.info(f"{'#' * 60}\n")

        metrics = await run_benchmark(project_name, variant, output_dir)
        results.append(metrics)

        # Save individual result
        result_file = os.path.join(output_dir, f"{variant}_{project_name}_results.json")
        with open(result_file, "w") as f:
            json.dump(metrics, f, indent=2)

        logger.info(f"\n{'=' * 60}")
        logger.info(f"RESULT: {project_name} ({variant})")
        logger.info(
            f"  Tasks: {metrics.get('tasks_succeeded', 0)}/{metrics.get('tasks_total', 0)} succeeded"
        )
        logger.info(
            f"  Tests: {metrics.get('tests_passed', 0)} passed, {metrics.get('tests_failed', 0)} failed"
        )
        logger.info(f"  Tokens: {metrics.get('total_tokens', 0)}")
        logger.info(f"  Time: {metrics.get('elapsed_seconds', 0)}s")
        logger.info(f"  Files: {metrics.get('files_count', 0)}")
        logger.info(f"{'=' * 60}\n")

    # Save combined results
    combined_file = os.path.join(output_dir, f"{variant}_combined_results.json")
    with open(combined_file, "w") as f:
        json.dump(results, f, indent=2)

    return results


def print_summary(results: list[dict]):
    """Print a formatted summary table of benchmark results."""
    print("\n" + "=" * 80)
    print(f"{'BENCHMARK RESULTS SUMMARY':^80}")
    print("=" * 80)
    print(f"{'Project':<20} {'Variant':<12} {'Tasks':<10} {'Tests':<15} {'Tokens':<10} {'Time':<8}")
    print("-" * 80)

    total_tests_passed = 0
    total_tests_total = 0
    total_tokens = 0

    for r in results:
        if "error" in r:
            print(f"{r['project']:<20} {r['variant']:<12} {'ERROR':<10} {r['error'][:30]}")
            continue

        tasks = f"{r['tasks_succeeded']}/{r['tasks_total']}"
        tests = f"{r['tests_passed']}/{r['tests_total']} passed"
        tokens = str(r["total_tokens"])
        time_s = f"{r['elapsed_seconds']}s"

        print(
            f"{r['project']:<20} {r['variant']:<12} {tasks:<10} {tests:<15} {tokens:<10} {time_s:<8}"
        )

        total_tests_passed += r.get("tests_passed", 0)
        total_tests_total += r.get("tests_total", 0)
        total_tokens += r.get("total_tokens", 0)

    print("-" * 80)
    print(
        f"{'TOTAL':<20} {'':<12} {'':<10} {total_tests_passed}/{total_tests_total} passed   {total_tokens:<10}"
    )
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run real HiveMind benchmarks")
    parser.add_argument(
        "--variant",
        default="baseline",
        choices=["baseline", "chromadb", "llmlingua", "review_loop"],
        help="Which variant to benchmark",
    )
    parser.add_argument(
        "--project",
        default=None,
        choices=list(PROJECTS.keys()),
        help="Run a single project (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for results",
    )
    args = parser.parse_args()

    if args.project:
        results = [asyncio.run(run_benchmark(args.project, args.variant, args.output_dir))]
    else:
        results = asyncio.run(run_all_benchmarks(args.variant, args.output_dir))

    print_summary(results)
