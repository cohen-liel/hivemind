#!/usr/bin/env python3
"""Run the enhanced LangGraph benchmark (LangGraph + all HiveMind features)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HIVEMIND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HIVEMIND_ROOT))
sys.path.insert(0, str(HIVEMIND_ROOT / "benchmarks"))

import isolated_query_openai
sys.modules["isolated_query"] = isolated_query_openai

from contracts import AgentRole, TaskGraph, TaskInput
from prompts import PROMPT_REGISTRY
from code_quality_scorer import score_project
from langgraph_executor_enhanced import execute_graph_langgraph_enhanced

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("langgraph_enhanced")

# Same 3 projects
PROJECTS = {
    "todo_api": {
        "description": "Build a Python FastAPI REST API for todo management with SQLite",
        "vision": "Create a production-quality REST API for managing todo items with full CRUD operations, SQLite persistence, and comprehensive test coverage",
        "epics": ["Set up FastAPI project with SQLite database", "Implement CRUD endpoints for todos", "Write comprehensive pytest test suite"],
        "tasks": [
            TaskInput(id="task_001", role=AgentRole.BACKEND_DEVELOPER,
                goal="Create a FastAPI REST API for todo management. Requirements:\n1. Create main.py with FastAPI app\n2. Create database.py with SQLite setup using sqlite3 (NOT SQLAlchemy)\n3. Create models.py with Pydantic models for Todo (id, title, description, completed, created_at)\n4. Implement these endpoints:\n   - GET /todos - list all todos\n   - POST /todos - create a todo\n   - GET /todos/{id} - get a single todo\n   - PUT /todos/{id} - update a todo\n   - DELETE /todos/{id} - delete a todo\n5. Use proper HTTP status codes (201 for create, 404 for not found)\n6. Initialize the database table on startup\nMake sure the app runs with: uvicorn main:app",
                depends_on=[], acceptance_criteria=["main.py exists", "database.py exists", "models.py exists", "App starts"]),
            TaskInput(id="task_002", role=AgentRole.TEST_ENGINEER,
                goal="Write comprehensive pytest tests for the todo API. Requirements:\n1. Create test_app.py with pytest tests using FastAPI TestClient\n2. Test ALL endpoints:\n   - Test creating a todo (POST /todos) - verify 201 status\n   - Test listing todos (GET /todos)\n   - Test getting a single todo (GET /todos/{id})\n   - Test updating a todo (PUT /todos/{id})\n   - Test deleting a todo (DELETE /todos/{id})\n   - Test getting non-existent todo (GET /todos/999) - verify 404\n3. Use a fresh test database for each test\n4. Run the tests with: python -m pytest test_app.py -v\nIMPORTANT: After writing tests, RUN them with pytest and fix any failures.",
                depends_on=["task_001"], context_from=["task_001"], acceptance_criteria=["test_app.py exists", "Tests pass"]),
        ],
    },
    "calculator_cli": {
        "description": "Build a Python CLI calculator with history and unit tests",
        "vision": "Create a command-line calculator application with operation history, undo functionality, and comprehensive test coverage",
        "epics": ["Implement calculator core", "Add history and undo", "Write test suite"],
        "tasks": [
            TaskInput(id="task_001", role=AgentRole.BACKEND_DEVELOPER,
                goal="Create a Python calculator module. Requirements:\n1. Create calculator.py with a Calculator class that supports:\n   - add(a, b) -> float\n   - subtract(a, b) -> float\n   - multiply(a, b) -> float\n   - divide(a, b) -> float (raise ValueError on division by zero)\n   - power(base, exp) -> float\n   - sqrt(n) -> float (raise ValueError for negative numbers)\n   - history property -> list of (operation, result) tuples\n   - undo() -> removes last operation from history\n   - clear_history() -> clears all history\n2. Each operation should be recorded in history\n3. The module should be importable: from calculator import Calculator",
                depends_on=[], acceptance_criteria=["calculator.py exists", "All operations work", "History works"]),
            TaskInput(id="task_002", role=AgentRole.TEST_ENGINEER,
                goal="Write comprehensive pytest tests for the calculator module. Requirements:\n1. Create test_calculator.py with pytest tests\n2. Test ALL operations: add, subtract, multiply, divide, power, sqrt\n3. Test edge cases: division by zero, sqrt of negative, large numbers, floating point\n4. Test history: operations recorded, undo works, clear works\n5. Run tests with: python -m pytest test_calculator.py -v\nIMPORTANT: After writing tests, RUN them with pytest and fix any failures.",
                depends_on=["task_001"], context_from=["task_001"], acceptance_criteria=["test_calculator.py exists", "Tests pass"]),
        ],
    },
    "url_shortener": {
        "description": "Build a URL shortener API with FastAPI and SQLite",
        "vision": "Create a URL shortener service with short code generation, redirect, analytics, and tests",
        "epics": ["Set up FastAPI with SQLite", "Implement shortening and redirect", "Add analytics", "Write tests"],
        "tasks": [
            TaskInput(id="task_001", role=AgentRole.BACKEND_DEVELOPER,
                goal="Create a URL shortener API with FastAPI. Requirements:\n1. Create main.py with FastAPI app\n2. Create database.py with SQLite setup (using sqlite3, NOT SQLAlchemy)\n3. Create models.py with Pydantic models\n4. Implement endpoints:\n   - POST /shorten - accepts {url: string}, returns {short_code: string, short_url: string}\n   - GET /{short_code} - redirects to original URL (307 redirect)\n   - GET /stats/{short_code} - returns {url, short_code, clicks, created_at}\n   - DELETE /{short_code} - deletes a shortened URL\n5. Generate random 6-character alphanumeric short codes\n6. Track click count for each URL\n7. Return 404 for non-existent short codes\nMake sure the app runs with: uvicorn main:app",
                depends_on=[], acceptance_criteria=["main.py with all endpoints", "database.py with SQLite", "Short codes work", "Click tracking works"]),
            TaskInput(id="task_002", role=AgentRole.TEST_ENGINEER,
                goal="Write comprehensive pytest tests for the URL shortener. Requirements:\n1. Create test_app.py with pytest tests using FastAPI TestClient\n2. Test ALL endpoints:\n   - Test shortening a URL (POST /shorten)\n   - Test redirect works (GET /{short_code}) - verify 307\n   - Test stats endpoint (GET /stats/{short_code})\n   - Test click count increments after redirect\n   - Test deleting a URL (DELETE /{short_code})\n   - Test 404 for non-existent code\n3. Use a fresh test database for each test\n4. Run tests with: python -m pytest test_app.py -v\nIMPORTANT: After writing tests, RUN them with pytest and fix any failures.",
                depends_on=["task_001"], context_from=["task_001"], acceptance_criteria=["test_app.py exists", "Tests pass"]),
        ],
    },
}


def _run_pytest(project_dir: str) -> dict:
    test_files = [f for f in os.listdir(project_dir) if f.startswith("test_") and f.endswith(".py")]
    if not test_files:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "output": "No test files found"}
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short", "--no-header"] + test_files,
            cwd=project_dir, capture_output=True, text=True, timeout=60,
        )
        output = result.stdout + "\n" + result.stderr
        passed = failed = errors = 0
        for line in output.split("\n"):
            m_p = re.search(r"(\d+) passed", line)
            m_f = re.search(r"(\d+) failed", line)
            m_e = re.search(r"(\d+) error", line)
            if m_p: passed = int(m_p.group(1))
            if m_f: failed = int(m_f.group(1))
            if m_e: errors = int(m_e.group(1))
        return {"passed": passed, "failed": failed, "errors": errors, "total": passed + failed + errors, "output": output[-2000:]}
    except Exception as e:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "output": str(e)}


async def main():
    output_dir = os.path.join(str(HIVEMIND_ROOT), "benchmarks", "results", f"oss_langgraph_enhanced_{int(time.time())}")
    os.makedirs(output_dir, exist_ok=True)

    results = []
    for project_name, project_def in PROJECTS.items():
        project_dir = os.path.join(output_dir, f"langgraph_enhanced_{project_name}")
        os.makedirs(project_dir, exist_ok=True)

        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "bench@test"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Bench"], cwd=project_dir, capture_output=True)
        os.makedirs(os.path.join(project_dir, ".hivemind"), exist_ok=True)

        graph = TaskGraph(
            project_id=f"bench_lg_enhanced_{project_name}",
            user_message=project_def["description"],
            vision=project_def["vision"],
            epic_breakdown=project_def["epics"],
            tasks=project_def["tasks"],
        )

        logger.info(f"\n{'#'*60}\n# LangGraph Enhanced: {project_name}\n{'#'*60}")

        t0 = time.monotonic()
        try:
            result = await execute_graph_langgraph_enhanced(graph, project_dir, PROMPT_REGISTRY)
        except Exception as e:
            logger.error(f"Failed: {e}", exc_info=True)
            results.append({"project": project_name, "variant": "langgraph_enhanced", "error": str(e)})
            continue
        elapsed = time.monotonic() - t0

        test_results = _run_pytest(project_dir)
        quality = score_project(project_dir)

        metrics = {
            "project": project_name,
            "variant": "langgraph_enhanced",
            "project_dir": project_dir,
            "elapsed_seconds": round(elapsed, 1),
            "total_tokens": result.total_tokens,
            "total_input_tokens": result.total_input_tokens,
            "total_output_tokens": result.total_output_tokens,
            "tasks_total": len(graph.tasks),
            "tasks_succeeded": result.success_count,
            "tasks_failed": result.failure_count,
            "tests_passed": test_results.get("passed", 0),
            "tests_failed": test_results.get("failed", 0),
            "tests_total": test_results.get("total", 0),
            "test_results": test_results,
            "code_quality": quality,
            "code_quality_score": quality.get("overall_score", 0),
        }
        results.append(metrics)

        with open(os.path.join(output_dir, f"langgraph_enhanced_{project_name}_results.json"), "w") as f:
            json.dump(metrics, f, indent=2, default=str)

        logger.info(f"\nRESULT: {project_name} — Tasks: {result.success_count}/{len(graph.tasks)}, "
                     f"Tests: {test_results['passed']}/{test_results['total']}, "
                     f"Tokens: {result.total_tokens}, Quality: {quality.get('overall_score', 'N/A')}/10")

    # Summary
    print(f"\n{'='*90}")
    print(f"{'LANGGRAPH ENHANCED BENCHMARK RESULTS':^90}")
    print(f"{'='*90}")
    print(f"{'Project':<18} {'Tasks':<8} {'Tests':<12} {'Tokens':<10} {'Time':<8} {'Quality':<8}")
    print("-" * 90)
    for r in results:
        if "error" in r:
            print(f"{r['project']:<18} ERROR: {r['error'][:50]}")
            continue
        print(f"{r['project']:<18} {r['tasks_succeeded']}/{r['tasks_total']:<6} "
              f"{r['tests_passed']}/{r['tests_total']:<10} {r['total_tokens']:<10} "
              f"{r['elapsed_seconds']}s{'':<4} {r.get('code_quality_score', 'N/A')}/10")
    print(f"{'='*90}")

    with open(os.path.join(output_dir, "combined_results.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    asyncio.run(main())
