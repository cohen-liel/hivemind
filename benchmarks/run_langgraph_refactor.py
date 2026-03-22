#!/usr/bin/env python3
"""Benchmark runner for the refactored LangGraph DAG executor.

Runs the same 3 projects (todo_api, calculator_cli, url_shortener) using
the LangGraph-based dag_executor_langgraph.py and compares results against
the baseline (original dag_executor.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HIVEMIND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HIVEMIND_ROOT))

# Monkey-patch isolated_query BEFORE importing anything else
import isolated_query_openai

sys.modules["isolated_query"] = isolated_query_openai

# Mock state module
import types

state_mod = types.ModuleType("state")
state_mod.sdk_client = "mock_sdk"
sys.modules["state"] = state_mod

from contracts import AgentRole, TaskGraph, TaskInput
from dag_executor_langgraph import execute_graph
from prompts import PROMPT_REGISTRY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("lg_benchmark")

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
                files_scope=["main.py", "database.py", "models.py"],
            ),
            TaskInput(
                id="task_002",
                role=AgentRole.TEST_ENGINEER,
                goal=(
                    "Write comprehensive pytest tests for the FastAPI todo API.\n"
                    "1. Create test_main.py with tests using httpx.AsyncClient and pytest-asyncio\n"
                    "2. Test ALL endpoints: GET /todos, POST /todos, GET /todos/{id}, PUT /todos/{id}, DELETE /todos/{id}\n"
                    "3. Test edge cases: 404 for missing todo, invalid input\n"
                    "4. Use a fresh test database for each test\n"
                    "5. Run the tests and make sure they ALL pass\n"
                    "Install any needed packages with pip."
                ),
                depends_on=["task_001"],
                context_from=["task_001"],
                files_scope=["test_main.py"],
            ),
        ],
    },
    "calculator_cli": {
        "description": "Build a Python CLI calculator with history",
        "vision": "Create a command-line calculator with expression parsing, history, and comprehensive tests",
        "epics": [
            "Implement calculator with expression parsing",
            "Add history feature",
            "Write comprehensive tests",
        ],
        "tasks": [
            TaskInput(
                id="task_001",
                role=AgentRole.BACKEND_DEVELOPER,
                goal=(
                    "Create a Python CLI calculator. Requirements:\n"
                    "1. Create calculator.py with a Calculator class that:\n"
                    "   - Evaluates math expressions (add, subtract, multiply, divide)\n"
                    "   - Handles parentheses and operator precedence\n"
                    "   - Stores calculation history (list of {expression, result} dicts)\n"
                    "   - Has methods: evaluate(expr), get_history(), clear_history()\n"
                    "   - Raises ValueError for invalid expressions\n"
                    "   - Raises ZeroDivisionError for division by zero\n"
                    "2. Create cli.py with an interactive REPL:\n"
                    "   - Prompt user for expressions\n"
                    "   - 'history' command shows past calculations\n"
                    "   - 'clear' command clears history\n"
                    "   - 'quit' or 'exit' to stop\n"
                    "Do NOT use eval() — implement proper expression parsing."
                ),
                depends_on=[],
                files_scope=["calculator.py", "cli.py"],
            ),
            TaskInput(
                id="task_002",
                role=AgentRole.TEST_ENGINEER,
                goal=(
                    "Write comprehensive pytest tests for the calculator.\n"
                    "1. Create test_calculator.py with tests for:\n"
                    "   - Basic operations: 2+3, 10-4, 3*5, 15/3\n"
                    "   - Operator precedence: 2+3*4 should be 14\n"
                    "   - Parentheses: (2+3)*4 should be 20\n"
                    "   - Negative numbers\n"
                    "   - Decimal numbers: 1.5+2.5\n"
                    "   - Division by zero (should raise ZeroDivisionError)\n"
                    "   - Invalid expressions (should raise ValueError)\n"
                    "   - History: after evaluating, history should contain the entry\n"
                    "   - Clear history: after clear, history should be empty\n"
                    "   - Complex expressions: ((2+3)*4-5)/3\n"
                    "2. Run the tests and make sure they ALL pass."
                ),
                depends_on=["task_001"],
                context_from=["task_001"],
                files_scope=["test_calculator.py"],
            ),
        ],
    },
    "url_shortener": {
        "description": "Build a URL shortener service with FastAPI and SQLite",
        "vision": "Create a URL shortener with short code generation, redirect, click tracking, and tests",
        "epics": [
            "Implement URL shortening with SQLite storage",
            "Add redirect and click tracking",
            "Write comprehensive tests",
        ],
        "tasks": [
            TaskInput(
                id="task_001",
                role=AgentRole.BACKEND_DEVELOPER,
                goal=(
                    "Create a URL shortener service with FastAPI. Requirements:\n"
                    "1. Create main.py with FastAPI app\n"
                    "2. Create database.py with SQLite setup using sqlite3\n"
                    "3. Create models.py with Pydantic models\n"
                    "4. Implement endpoints:\n"
                    "   - POST /shorten - accepts {url: string}, returns {short_code: string, short_url: string}\n"
                    "   - GET /{short_code} - redirects to original URL (HTTP 307)\n"
                    "   - GET /stats/{short_code} - returns {original_url, short_code, clicks, created_at}\n"
                    "5. Generate random 6-char alphanumeric short codes\n"
                    "6. Track click count (increment on each redirect)\n"
                    "7. Return 404 for unknown short codes\n"
                    "Make sure the app runs with: uvicorn main:app"
                ),
                depends_on=[],
                files_scope=["main.py", "database.py", "models.py"],
            ),
            TaskInput(
                id="task_002",
                role=AgentRole.TEST_ENGINEER,
                goal=(
                    "Write comprehensive pytest tests for the URL shortener.\n"
                    "1. Create test_main.py with tests using httpx.AsyncClient\n"
                    "2. Test ALL endpoints:\n"
                    "   - POST /shorten with valid URL\n"
                    "   - GET /{short_code} redirect\n"
                    "   - GET /stats/{short_code} returns correct data\n"
                    "   - Click count increments on redirect\n"
                    "   - 404 for unknown short codes\n"
                    "3. Use a fresh test database for each test\n"
                    "4. Run the tests and make sure they ALL pass\n"
                    "Install any needed packages with pip."
                ),
                depends_on=["task_001"],
                context_from=["task_001"],
                files_scope=["test_main.py"],
            ),
        ],
    },
}


def run_pytest(project_dir: str) -> dict:
    """Run pytest in the project directory and return results."""
    try:
        result = subprocess.run(
            ["python3", "-m", "pytest", "-v", "--tb=short", "--no-header"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + "\n" + result.stderr

        passed = 0
        failed = 0
        errors = 0
        for line in output.split("\n"):
            if " passed" in line:
                import re

                m = re.search(r"(\d+) passed", line)
                if m:
                    passed = int(m.group(1))
            if " failed" in line:
                import re

                m = re.search(r"(\d+) failed", line)
                if m:
                    failed = int(m.group(1))
            if " error" in line:
                import re

                m = re.search(r"(\d+) error", line)
                if m:
                    errors = int(m.group(1))

        return {
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "total": passed + failed + errors,
            "returncode": result.returncode,
            "output": output[-3000:],  # Last 3K chars
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": 0,
            "failed": 0,
            "errors": 1,
            "total": 1,
            "returncode": -1,
            "output": "pytest timed out after 120s",
        }
    except Exception as e:
        return {
            "passed": 0,
            "failed": 0,
            "errors": 1,
            "total": 1,
            "returncode": -1,
            "output": f"Error running pytest: {e}",
        }


async def run_project(project_name: str, results_dir: str) -> dict:
    """Run a single project through the LangGraph DAG executor."""
    project_def = PROJECTS[project_name]
    project_dir = os.path.join(results_dir, f"langgraph_{project_name}")

    # Clean start
    if os.path.exists(project_dir):
        shutil.rmtree(project_dir)
    os.makedirs(project_dir, exist_ok=True)

    # Init git
    subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "bench@test.com"],
        cwd=project_dir,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Benchmark"],
        cwd=project_dir,
        capture_output=True,
    )

    # Build TaskGraph
    graph = TaskGraph(
        project_id=f"bench_{project_name}",
        user_message=project_def["description"],
        vision=project_def["vision"],
        epic_breakdown=project_def["epics"],
        tasks=project_def["tasks"],
    )

    # Get specialist prompts
    specialist_prompts = {}
    for role in AgentRole:
        if role.value in PROMPT_REGISTRY:
            specialist_prompts[role.value] = PROMPT_REGISTRY[role.value]

    t0 = time.time()
    logger.info(f"\n{'=' * 60}\n  Running: {project_name} (LangGraph)\n{'=' * 60}")

    try:
        result = await execute_graph(
            graph=graph,
            project_dir=project_dir,
            specialist_prompts=specialist_prompts,
            sdk_client="mock_sdk",
            max_budget_usd=10.0,
            max_concurrent_tasks=2,
        )
    except Exception as e:
        logger.error(f"execute_graph failed: {e}", exc_info=True)
        return {
            "project": project_name,
            "error": str(e),
            "elapsed_seconds": time.time() - t0,
        }

    elapsed = time.time() - t0

    # Run pytest
    pytest_results = run_pytest(project_dir)

    # Count files
    files = []
    for root, dirs, filenames in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache")]
        for f in filenames:
            if f.endswith((".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".md")):
                files.append(os.path.relpath(os.path.join(root, f), project_dir))

    summary = {
        "project": project_name,
        "executor": "langgraph",
        "tasks_total": len(graph.tasks),
        "tasks_succeeded": result.success_count,
        "tasks_failed": result.failure_count,
        "remediations": result.remediation_count,
        "tests_passed": pytest_results["passed"],
        "tests_failed": pytest_results["failed"],
        "tests_errors": pytest_results["errors"],
        "tests_total": pytest_results["total"],
        "total_tokens": result.total_tokens,
        "total_cost_usd": result.total_cost,
        "elapsed_seconds": round(elapsed, 1),
        "files_created": files,
        "healing_history": result.healing_history,
        "pytest_output": pytest_results["output"],
    }

    # Save results
    with open(os.path.join(results_dir, f"langgraph_{project_name}_result.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    logger.info(
        f"\n  {project_name} Results:\n"
        f"    Tasks: {result.success_count}/{len(graph.tasks)} succeeded\n"
        f"    Tests: {pytest_results['passed']}/{pytest_results['total']} passed\n"
        f"    Tokens: {result.total_tokens:,}\n"
        f"    Time: {elapsed:.1f}s\n"
        f"    Remediations: {result.remediation_count}\n"
        f"    Files: {files}\n"
    )

    return summary


async def main():
    results_dir = os.path.join(HIVEMIND_ROOT, "benchmarks", "results", "langgraph_refactor")
    os.makedirs(results_dir, exist_ok=True)

    # Set up logging to file
    fh = logging.FileHandler(os.path.join(results_dir, "benchmark.log"))
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    logging.getLogger().addHandler(fh)

    all_results = []

    for project_name in ["todo_api", "calculator_cli", "url_shortener"]:
        result = await run_project(project_name, results_dir)
        all_results.append(result)

    # Save combined results
    with open(os.path.join(results_dir, "all_results.json"), "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    # Print summary table
    print("\n" + "=" * 80)
    print("  LANGGRAPH REFACTOR BENCHMARK RESULTS")
    print("=" * 80)
    print(f"{'Project':<20} {'Tasks':>10} {'Tests':>15} {'Tokens':>10} {'Time':>8}")
    print("-" * 80)

    total_tests_passed = 0
    total_tests_total = 0
    total_tokens = 0

    for r in all_results:
        if "error" in r:
            print(f"{r['project']:<20} {'ERROR':>10} {r.get('error', '')[:40]}")
            continue
        tests_str = f"{r['tests_passed']}/{r['tests_total']}"
        tasks_str = f"{r['tasks_succeeded']}/{r['tasks_total']}"
        print(
            f"{r['project']:<20} {tasks_str:>10} {tests_str:>15} "
            f"{r['total_tokens']:>10,} {r['elapsed_seconds']:>7.1f}s"
        )
        total_tests_passed += r["tests_passed"]
        total_tests_total += r["tests_total"]
        total_tokens += r["total_tokens"]

    print("-" * 80)
    print(
        f"{'TOTAL':<20} {'':>10} {total_tests_passed}/{total_tests_total}:>15 {total_tokens:>10,}"
    )
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
