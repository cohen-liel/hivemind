#!/usr/bin/env python3
"""OSS A/B Benchmark Runner — tests open-source tools against the baseline.

Variants:
- langgraph: Use LangGraph StateGraph instead of custom dag_executor
- chromadb_memory: Use ChromaDB for cross-project memory retrieval
- langchain_agent: Use LangChain ReAct agent instead of custom isolated_query

Each variant runs the same 3 projects and collects:
- Test pass rate
- Token usage
- Time
- Code quality score (via LLM review)

Usage:
    python3 benchmarks/oss_benchmark.py --variant langgraph
    python3 benchmarks/oss_benchmark.py --variant chromadb_memory
    python3 benchmarks/oss_benchmark.py --variant langchain_agent
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

# Monkey-patch isolated_query
import isolated_query_openai

sys.modules["isolated_query"] = isolated_query_openai

from code_quality_scorer import score_project

from contracts import AgentRole, TaskGraph, TaskInput
from prompts import PROMPT_REGISTRY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("oss_benchmark")


# ── Same 3 Test Projects ──────────────────────────────────────────────────

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
                    "   - Test redirect works (GET /{short_code}) - verify 307\n"
                    "   - Test stats endpoint (GET /stats/{short_code})\n"
                    "   - Test click count increments after redirect\n"
                    "   - Test deleting a URL (DELETE /{short_code})\n"
                    "   - Test 404 for non-existent code\n"
                    "3. Use a fresh test database for each test\n"
                    "4. Run tests with: python -m pytest test_app.py -v\n"
                    "IMPORTANT: After writing tests, RUN them with pytest and fix any failures."
                ),
                depends_on=["task_001"],
                context_from=["task_001"],
                acceptance_criteria=[
                    "test_app.py exists with comprehensive tests",
                    "Tests pass when run with pytest",
                    "At least 6 test functions",
                ],
            ),
        ],
    },
}


# ── ChromaDB Memory Retrieval ──────────────────────────────────────────────


def _get_chromadb_lessons(task_goal: str) -> str:
    """Use ChromaDB to retrieve relevant lessons based on semantic similarity."""
    import chromadb

    # Create/load a persistent ChromaDB collection with sample lessons
    client = chromadb.Client()
    collection = client.get_or_create_collection(
        name="project_lessons",
        metadata={"hnsw:space": "cosine"},
    )

    # Seed with lessons if empty
    if collection.count() == 0:
        lessons = [
            {
                "id": "1",
                "text": "Always use parameterized SQL queries to prevent SQL injection. Never use f-strings for SQL.",
                "category": "security",
            },
            {
                "id": "2",
                "text": "Use logging.getLogger(__name__) instead of print() for all output. Configure logging at app startup.",
                "category": "logging",
            },
            {
                "id": "3",
                "text": "Always define custom exception classes (e.g., ResourceNotFoundError) instead of using generic ValueError/RuntimeError.",
                "category": "error_handling",
            },
            {
                "id": "4",
                "text": "Add type hints to ALL function signatures. This catches bugs early and improves IDE support.",
                "category": "type_safety",
            },
            {
                "id": "5",
                "text": "Use constants for magic numbers. Define SHORT_CODE_LENGTH = 6 instead of hardcoding 6 everywhere.",
                "category": "constants",
            },
            {
                "id": "6",
                "text": "Always validate input at API boundaries using Pydantic validators. Don't trust any external input.",
                "category": "validation",
            },
            {
                "id": "7",
                "text": "Write docstrings for all public functions using Google-style format (Args, Returns, Raises).",
                "category": "documentation",
            },
            {
                "id": "8",
                "text": "Use context managers (with statements) for all resource management (files, DB connections).",
                "category": "resource_management",
            },
            {
                "id": "9",
                "text": "Separate database operations into their own functions. Don't mix SQL with business logic.",
                "category": "architecture",
            },
            {
                "id": "10",
                "text": "In tests, always use isolated test databases (tmp_path) and clean up after each test.",
                "category": "testing",
            },
            {
                "id": "11",
                "text": "When building REST APIs, always return consistent error response formats with error codes and messages.",
                "category": "api_design",
            },
            {
                "id": "12",
                "text": "Use Pydantic's Field() with description and examples for API documentation.",
                "category": "api_design",
            },
            {
                "id": "13",
                "text": "Always handle database connection errors gracefully with retry logic.",
                "category": "reliability",
            },
            {
                "id": "14",
                "text": "Use HTTP 201 for resource creation, 204 for deletion, 404 for not found.",
                "category": "http_standards",
            },
            {
                "id": "15",
                "text": "Write integration tests that test the full request-response cycle, not just unit tests.",
                "category": "testing",
            },
        ]
        collection.add(
            documents=[l["text"] for l in lessons],
            metadatas=[{"category": l["category"]} for l in lessons],
            ids=[l["id"] for l in lessons],
        )

    # Query for relevant lessons
    results = collection.query(
        query_texts=[task_goal],
        n_results=5,
    )

    if results and results["documents"]:
        lessons_text = "\n".join(f"- {doc}" for doc in results["documents"][0])
        return lessons_text

    return ""


# ── LangChain Agent ────────────────────────────────────────────────────────


async def _run_langchain_agent(
    prompt: str,
    system_prompt: str,
    cwd: str,
    max_turns: int = 30,
) -> dict:
    """Run a LangChain ReAct agent with the same tools."""
    # Import our tool executors
    from isolated_query_openai import (
        _exec_bash,
        _exec_edit,
        _exec_glob,
        _exec_grep,
        _exec_read,
        _exec_write,
    )
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI

    # Define LangChain tools wrapping our executors
    @tool
    def read_file(file_path: str, start_line: int = 0, end_line: int = 0) -> str:
        """Read the contents of a file. Returns the full file content."""
        args = {"file_path": file_path}
        if start_line > 0:
            args["start_line"] = start_line
        if end_line > 0:
            args["end_line"] = end_line
        return _exec_read(cwd, args)

    @tool
    def write_file(file_path: str, content: str) -> str:
        """Create or overwrite a file with the given content."""
        return _exec_write(cwd, {"file_path": file_path, "content": content})

    @tool
    def edit_file(file_path: str, old_string: str, new_string: str) -> str:
        """Edit a file by replacing an exact string match with new content."""
        return _exec_edit(
            cwd, {"file_path": file_path, "old_string": old_string, "new_string": new_string}
        )

    @tool
    def bash(command: str, timeout: int = 120) -> str:
        """Execute a bash command in the project directory."""
        return _exec_bash(cwd, {"command": command, "timeout": timeout})

    @tool
    def glob_files(pattern: str) -> str:
        """Find files matching a glob pattern."""
        return _exec_glob(cwd, {"pattern": pattern})

    @tool
    def grep_search(pattern: str, path: str = ".", include: str = "") -> str:
        """Search file contents using a regex pattern."""
        args = {"pattern": pattern, "path": path}
        if include:
            args["include"] = include
        return _exec_grep(cwd, args)

    tools_list = [read_file, write_file, edit_file, bash, glob_files, grep_search]

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0.2, max_tokens=16000)
    llm_with_tools = llm.bind_tools(tools_list)

    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    messages.append(HumanMessage(content=prompt))

    total_input_tokens = 0
    total_output_tokens = 0
    tool_uses = []
    text_parts = []
    t0 = time.monotonic()

    tool_map = {t.name: t for t in tools_list}

    for turn in range(max_turns):
        response = llm_with_tools.invoke(messages)

        # Track tokens from response metadata
        if hasattr(response, "response_metadata"):
            usage = response.response_metadata.get("token_usage", {})
            total_input_tokens += usage.get("prompt_tokens", 0)
            total_output_tokens += usage.get("completion_tokens", 0)

        messages.append(response)

        if response.content and isinstance(response.content, str):
            text_parts.append(response.content)

        # Check for tool calls
        if not response.tool_calls:
            break

        # Execute tool calls
        from langchain_core.messages import ToolMessage

        for tc in response.tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]
            tool_uses.append(tool_name)

            logger.info(f"  [LangChain Turn {turn}] {tool_name}: {str(tool_args)[:80]}")

            try:
                tool_fn = tool_map[tool_name]
                result = tool_fn.invoke(tool_args)
            except Exception as e:
                result = f"Error: {e}"

            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    elapsed = time.monotonic() - t0

    return {
        "text": "\n".join(text_parts),
        "tokens": total_input_tokens + total_output_tokens,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "time": round(elapsed, 1),
        "turns": turn + 1 if "turn" in dir() else 0,
        "tool_uses": tool_uses,
    }


# ── Benchmark Runner ───────────────────────────────────────────────────────


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


async def run_langgraph_benchmark(project_name: str, output_dir: str) -> dict:
    """Run a project using LangGraph DAG executor."""
    from langgraph_executor import execute_graph_langgraph

    project_def = PROJECTS[project_name]
    project_dir = os.path.join(output_dir, f"langgraph_{project_name}")
    os.makedirs(project_dir, exist_ok=True)

    # Init git
    subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "bench@test"], cwd=project_dir, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "Bench"], cwd=project_dir, capture_output=True)
    os.makedirs(os.path.join(project_dir, ".hivemind"), exist_ok=True)

    graph = TaskGraph(
        project_id=f"bench_langgraph_{project_name}",
        user_message=project_def["description"],
        vision=project_def["vision"],
        epic_breakdown=project_def["epics"],
        tasks=project_def["tasks"],
    )

    t0 = time.monotonic()
    result = await execute_graph_langgraph(graph, project_dir, PROMPT_REGISTRY)
    elapsed = time.monotonic() - t0

    test_results = _run_pytest(project_dir)
    quality = score_project(project_dir)

    return {
        "project": project_name,
        "variant": "langgraph",
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


async def run_chromadb_benchmark(project_name: str, output_dir: str) -> dict:
    """Run a project with ChromaDB-powered memory injection."""
    from dag_executor_langgraph import ExecutionResult, execute_graph

    project_def = PROJECTS[project_name]
    project_dir = os.path.join(output_dir, f"chromadb_{project_name}")
    os.makedirs(project_dir, exist_ok=True)

    subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "bench@test"], cwd=project_dir, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "Bench"], cwd=project_dir, capture_output=True)
    os.makedirs(os.path.join(project_dir, ".hivemind"), exist_ok=True)

    # Enhance tasks with ChromaDB-retrieved lessons
    enhanced_tasks = []
    for task in project_def["tasks"]:
        lessons = _get_chromadb_lessons(task.goal)
        if lessons:
            enhanced_goal = (
                f"{task.goal}\n\n"
                f"<relevant_lessons_from_previous_projects>\n"
                f"Apply these lessons learned from similar projects:\n{lessons}\n"
                f"</relevant_lessons_from_previous_projects>"
            )
        else:
            enhanced_goal = task.goal

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

    graph = TaskGraph(
        project_id=f"bench_chromadb_{project_name}",
        user_message=project_def["description"],
        vision=project_def["vision"],
        epic_breakdown=project_def["epics"],
        tasks=enhanced_tasks,
    )

    t0 = time.monotonic()
    try:
        result: ExecutionResult = await execute_graph(
            graph=graph,
            project_dir=project_dir,
            specialist_prompts=PROMPT_REGISTRY,
            sdk_client=DummySDK(),
            max_budget_usd=10.0,
            max_concurrent_tasks=1,
        )
    except Exception as e:
        logger.error(f"ChromaDB benchmark failed: {e}")
        return {"project": project_name, "variant": "chromadb_memory", "error": str(e)}

    elapsed = time.monotonic() - t0
    test_results = _run_pytest(project_dir)
    quality = score_project(project_dir)

    return {
        "project": project_name,
        "variant": "chromadb_memory",
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


async def run_langchain_benchmark(project_name: str, output_dir: str) -> dict:
    """Run a project using LangChain ReAct agent instead of custom isolated_query."""
    project_def = PROJECTS[project_name]
    project_dir = os.path.join(output_dir, f"langchain_{project_name}")
    os.makedirs(project_dir, exist_ok=True)

    subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "bench@test"], cwd=project_dir, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "Bench"], cwd=project_dir, capture_output=True)
    os.makedirs(os.path.join(project_dir, ".hivemind"), exist_ok=True)

    total_tokens = 0
    total_input = 0
    total_output = 0
    successes = 0
    failures = 0
    task_results_map = {}

    t0 = time.monotonic()

    for task in project_def["tasks"]:
        # Wait for dependencies
        for dep_id in task.depends_on or []:
            if dep_id not in task_results_map:
                logger.warning(f"Dependency {dep_id} not found for {task.id}")

        # Build prompt
        role_str = str(task.role)
        system_prompt = PROMPT_REGISTRY.get(role_str, "")
        system_prompt += f"\n\nPROJECT BOUNDARY: {project_dir}\nWork ONLY within this directory."

        prompt_parts = [task.goal]
        for dep_id in getattr(task, "context_from", []) or []:
            dep_result = task_results_map.get(dep_id, {})
            if dep_result.get("text"):
                prompt_parts.append(
                    f"\n\n<context from {dep_id}>\n{dep_result['text'][:2000]}\n</context>"
                )
        prompt = "\n".join(prompt_parts)

        logger.info(f"[LangChain] Executing task {task.id} ({role_str})")

        try:
            result = await _run_langchain_agent(
                prompt=prompt,
                system_prompt=system_prompt,
                cwd=project_dir,
                max_turns=30,
            )
            task_results_map[task.id] = result
            total_tokens += result["tokens"]
            total_input += result["input_tokens"]
            total_output += result["output_tokens"]
            successes += 1
            logger.info(
                f"[LangChain] Task {task.id}: done "
                f"({result['turns']} turns, {result['tokens']} tokens, {result['time']}s)"
            )
        except Exception as e:
            logger.error(f"[LangChain] Task {task.id} failed: {e}")
            task_results_map[task.id] = {"text": str(e), "tokens": 0}
            failures += 1

    elapsed = time.monotonic() - t0
    test_results = _run_pytest(project_dir)
    quality = score_project(project_dir)

    return {
        "project": project_name,
        "variant": "langchain_agent",
        "project_dir": project_dir,
        "elapsed_seconds": round(elapsed, 1),
        "total_tokens": total_tokens,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "tasks_total": len(project_def["tasks"]),
        "tasks_succeeded": successes,
        "tasks_failed": failures,
        "tests_passed": test_results.get("passed", 0),
        "tests_failed": test_results.get("failed", 0),
        "tests_total": test_results.get("total", 0),
        "test_results": test_results,
        "code_quality": quality,
        "code_quality_score": quality.get("overall_score", 0),
    }


VARIANT_RUNNERS = {
    "langgraph": run_langgraph_benchmark,
    "chromadb_memory": run_chromadb_benchmark,
    "langchain_agent": run_langchain_benchmark,
}


async def run_all(variant: str) -> list[dict]:
    """Run all projects for a variant."""
    output_dir = os.path.join(
        str(HIVEMIND_ROOT), "benchmarks", "results", f"oss_{variant}_{int(time.time())}"
    )
    os.makedirs(output_dir, exist_ok=True)

    runner = VARIANT_RUNNERS[variant]
    results = []

    for project_name in PROJECTS:
        logger.info(f"\n{'#' * 60}")
        logger.info(f"# OSS Benchmark: {project_name} ({variant})")
        logger.info(f"{'#' * 60}\n")

        metrics = await runner(project_name, output_dir)
        results.append(metrics)

        # Save individual result
        result_file = os.path.join(output_dir, f"{variant}_{project_name}_results.json")
        with open(result_file, "w") as f:
            json.dump(metrics, f, indent=2, default=str)

        if "error" not in metrics:
            logger.info(f"\n{'=' * 60}")
            logger.info(f"RESULT: {project_name} ({variant})")
            logger.info(
                f"  Tasks: {metrics.get('tasks_succeeded', 0)}/{metrics.get('tasks_total', 0)}"
            )
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
    print(f"\n{'=' * 90}")
    print(f"{'OSS BENCHMARK RESULTS':^90}")
    print(f"{'=' * 90}")
    print(
        f"{'Project':<18} {'Variant':<18} {'Tasks':<8} {'Tests':<12} {'Tokens':<10} {'Time':<8} {'Quality':<8}"
    )
    print("-" * 90)

    for r in results:
        if "error" in r:
            print(f"{r['project']:<18} {r['variant']:<18} ERROR: {r.get('error', '')[:40]}")
            continue
        tasks = f"{r['tasks_succeeded']}/{r['tasks_total']}"
        tests = f"{r['tests_passed']}/{r['tests_total']}"
        tokens = str(r["total_tokens"])
        time_s = f"{r['elapsed_seconds']}s"
        quality = f"{r.get('code_quality_score', 'N/A')}/10"
        print(
            f"{r['project']:<18} {r['variant']:<18} {tasks:<8} {tests:<12} {tokens:<10} {time_s:<8} {quality:<8}"
        )

    print(f"{'=' * 90}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OSS A/B Benchmark Runner")
    parser.add_argument(
        "--variant",
        required=True,
        choices=list(VARIANT_RUNNERS.keys()),
        help="Which OSS variant to test",
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
            str(HIVEMIND_ROOT), "benchmarks", "results", f"oss_{args.variant}_{int(time.time())}"
        )
        os.makedirs(output_dir, exist_ok=True)
        runner = VARIANT_RUNNERS[args.variant]
        result = asyncio.run(runner(args.project, output_dir))
        print(json.dumps(result, indent=2, default=str))
    else:
        asyncio.run(run_all(args.variant))
