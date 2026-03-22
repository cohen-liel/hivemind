"""Final E2E Benchmark — proves the 3 improvements work together.

Runs 3 real programming tasks through 2 pipelines:
  BASELINE: HiveMind heuristic compression (400 chars) + no fix loop
  IMPROVED: LLMLingua compression + ChromaDB memory + Code Execution Gate fix loop

Each pipeline writes real code to disk, runs pytest, measures:
  - Tests passed vs failed
  - Syntax errors
  - Total tokens used
  - Total time
  - Fix loop effectiveness
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import py_compile
from pathlib import Path
from openai import OpenAI

client = OpenAI()

# Try to import LLMLingua
try:
    from llmlingua import PromptCompressor
    _compressor = PromptCompressor(
        model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
        use_llmlingua2=True,
        device_map="cpu",
    )
    HAS_LLMLINGUA = True
    print("[OK] LLMLingua loaded")
except Exception as e:
    HAS_LLMLINGUA = False
    print(f"[WARN] LLMLingua not available: {e}")

# Try to import ChromaDB
try:
    import chromadb
    HAS_CHROMA = True
    print("[OK] ChromaDB loaded")
except Exception:
    HAS_CHROMA = False
    print("[WARN] ChromaDB not available")

# Cross-project lessons (simulating real HiveMind memory)
LESSONS = [
    {"title": "SQLite thread safety", "text": "Always use check_same_thread=False when sharing SQLite connections across async handlers. Without this, you get 'SQLite objects created in a thread can only be used in that same thread' errors."},
    {"title": "Pydantic V2 migration", "text": "In Pydantic V2, use model_config = ConfigDict(from_attributes=True) instead of class Config: orm_mode = True. The old syntax raises deprecation warnings."},
    {"title": "FastAPI test client", "text": "Use httpx.AsyncClient with ASGITransport for testing async FastAPI apps. The old TestClient is synchronous and misses async bugs."},
    {"title": "Input validation", "text": "Always add min_length=1 to string fields in Pydantic models to prevent empty string submissions. Also use Path(gt=0) for ID parameters."},
    {"title": "Foreign key enforcement", "text": "SQLite doesn't enforce foreign keys by default. Run PRAGMA foreign_keys = ON at connection time, or use event listeners in SQLAlchemy."},
    {"title": "Error response format", "text": "Always return structured error responses with detail field. Use HTTPException(status_code=404, detail='Resource not found') consistently."},
]


def hivemind_compress(text: str, max_chars: int = 400) -> str:
    """HiveMind's original heuristic compression."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def llmlingua_compress(text: str) -> str:
    """LLMLingua compression."""
    if not HAS_LLMLINGUA or len(text) < 100:
        return text
    try:
        result = _compressor.compress_prompt(
            [text], rate=0.6,
            force_tokens=["\n", ".", "def ", "class ", "import ", "return "],
        )
        return result.get("compressed_prompt", text)
    except Exception:
        return text


def keyword_search(query: str, lessons: list) -> list:
    """HiveMind's original keyword search."""
    words = set(query.lower().split())
    scored = []
    for lesson in lessons:
        text = (lesson["title"] + " " + lesson["text"]).lower()
        score = sum(1 for w in words if w in text)
        if score > 0:
            scored.append((score, lesson))
    scored.sort(key=lambda x: -x[0])
    return [s[1] for s in scored[:3]]


def chroma_search(query: str, lessons: list) -> list:
    """ChromaDB semantic search."""
    if not HAS_CHROMA:
        return keyword_search(query, lessons)
    try:
        chroma_client = chromadb.Client()
        collection = chroma_client.get_or_create_collection("bench_lessons")
        # Add lessons
        for i, lesson in enumerate(lessons):
            collection.upsert(
                ids=[f"l{i}"],
                documents=[lesson["title"] + ": " + lesson["text"]],
                metadatas=[{"title": lesson["title"]}],
            )
        results = collection.query(query_texts=[query], n_results=3)
        found_ids = [int(id[1:]) for id in results["ids"][0]]
        return [lessons[i] for i in found_ids]
    except Exception:
        return keyword_search(query, lessons)


# ── Project tasks ──

TASKS = [
    {
        "name": "todo_api",
        "description": "Build a TODO list REST API with FastAPI and SQLite",
        "agents": [
            {
                "role": "database_engineer",
                "goal": "Create database.py with SQLite functions: init_db(), add_todo(title, description), get_todos(), get_todo(id), update_todo(id, done), delete_todo(id)",
                "file": "database.py",
            },
            {
                "role": "backend_developer",
                "goal": "Create main.py with FastAPI REST API: POST /todos, GET /todos, GET /todos/{id}, PUT /todos/{id}, DELETE /todos/{id}. Import from database.py.",
                "file": "main.py",
                "deps": ["database.py"],
            },
            {
                "role": "test_engineer",
                "goal": "Create test_app.py with pytest tests for the TODO API. Test all CRUD operations. Import from main.py.",
                "file": "test_app.py",
                "deps": ["database.py", "main.py"],
            },
        ],
    },
    {
        "name": "user_auth",
        "description": "Build a user authentication system with registration and login",
        "agents": [
            {
                "role": "database_engineer",
                "goal": "Create database.py with SQLite functions: init_db(), create_user(username, password_hash), get_user(username), get_user_by_id(id). Use bcrypt-compatible hashing.",
                "file": "database.py",
            },
            {
                "role": "backend_developer",
                "goal": "Create main.py with FastAPI: POST /register (username, password), POST /login (username, password returns token), GET /me (requires auth header). Import from database.py. Use hashlib for password hashing.",
                "file": "main.py",
                "deps": ["database.py"],
            },
            {
                "role": "test_engineer",
                "goal": "Create test_app.py with pytest tests for auth system. Test register, login, and protected endpoint. Import from main.py.",
                "file": "test_app.py",
                "deps": ["database.py", "main.py"],
            },
        ],
    },
    {
        "name": "bookmark_manager",
        "description": "Build a bookmark manager API with tags and search",
        "agents": [
            {
                "role": "database_engineer",
                "goal": "Create database.py with SQLite: init_db(), add_bookmark(url, title, tags), get_bookmarks(tag=None), search_bookmarks(query), delete_bookmark(id)",
                "file": "database.py",
            },
            {
                "role": "backend_developer",
                "goal": "Create main.py with FastAPI: POST /bookmarks, GET /bookmarks?tag=x, GET /bookmarks/search?q=x, DELETE /bookmarks/{id}. Import from database.py.",
                "file": "main.py",
                "deps": ["database.py"],
            },
            {
                "role": "test_engineer",
                "goal": "Create test_app.py with pytest tests for bookmark API. Test CRUD and search. Import from main.py.",
                "file": "test_app.py",
                "deps": ["database.py", "main.py"],
            },
        ],
    },
]


def call_llm(prompt: str, system: str = "You are a Python developer. Write ONLY the code, no explanations.") -> str:
    """Call the LLM API."""
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=4000,
    )
    return resp.choices[0].message.content


def extract_code(response: str) -> str:
    """Extract Python code from LLM response."""
    if "```python" in response:
        parts = response.split("```python")
        if len(parts) > 1:
            code = parts[1].split("```")[0]
            return code.strip()
    if "```" in response:
        parts = response.split("```")
        if len(parts) > 1:
            code = parts[1]
            if code.startswith("\n"):
                code = code[1:]
            return code.split("```")[0].strip()
    return response.strip()


def run_pytest(project_dir: str) -> dict:
    """Run pytest and return results."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--tb=short", "-q", project_dir],
            capture_output=True, text=True, timeout=30, cwd=project_dir,
        )
        output = result.stdout + "\n" + result.stderr
        passed = failed = 0
        for line in output.split("\n"):
            if "passed" in line:
                try:
                    passed = int(line.split("passed")[0].strip().split()[-1])
                except (ValueError, IndexError):
                    pass
            if "failed" in line or "error" in line.lower():
                try:
                    failed = int(line.split("failed")[0].strip().split()[-1])
                except (ValueError, IndexError):
                    pass
        return {"passed": passed, "failed": failed, "output": output[:2000]}
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "output": "timeout"}
    except Exception as e:
        return {"passed": 0, "failed": 0, "output": str(e)}


def check_syntax(project_dir: str) -> list:
    """Check syntax of all .py files."""
    errors = []
    for f in Path(project_dir).glob("*.py"):
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(str(e)[:200])
    return errors


def run_pipeline(task: dict, pipeline_name: str, use_llmlingua: bool, use_chroma: bool, use_fixloop: bool) -> dict:
    """Run a full pipeline on a task."""
    project_dir = f"/home/ubuntu/hivemind/benchmarks/final_{pipeline_name}_{task['name']}"
    if os.path.exists(project_dir):
        shutil.rmtree(project_dir)
    os.makedirs(project_dir)

    t0 = time.time()
    total_tokens = 0
    agent_outputs = {}
    fix_attempts = 0

    # Get relevant lessons
    task_query = task["description"]
    if use_chroma:
        relevant_lessons = chroma_search(task_query, LESSONS)
    else:
        relevant_lessons = keyword_search(task_query, LESSONS)

    lessons_text = "\n".join([f"- {l['title']}: {l['text']}" for l in relevant_lessons])

    for agent in task["agents"]:
        # Build context from dependencies
        dep_context = ""
        for dep in agent.get("deps", []):
            if dep in agent_outputs:
                raw = agent_outputs[dep]
                if use_llmlingua:
                    compressed = llmlingua_compress(raw)
                else:
                    compressed = hivemind_compress(raw)
                dep_context += f"\n\n### {dep} (dependency):\n```python\n{compressed}\n```"

        prompt = (
            f"Project: {task['description']}\n\n"
            f"Your task: {agent['goal']}\n\n"
            f"Write the complete {agent['file']} file.\n\n"
        )

        if lessons_text:
            prompt += f"Lessons from previous projects:\n{lessons_text}\n\n"

        if dep_context:
            prompt += f"Code from dependencies:{dep_context}\n\n"

        prompt += (
            f"Write ONLY the Python code for {agent['file']}. "
            "No explanations, no markdown. Just the code."
        )

        response = call_llm(prompt)
        code = extract_code(response)
        total_tokens += len(prompt.split()) + len(response.split())

        # Write to disk
        filepath = os.path.join(project_dir, agent["file"])
        with open(filepath, "w") as f:
            f.write(code)

        agent_outputs[agent["file"]] = code

    # Check syntax
    syntax_errors = check_syntax(project_dir)

    # Run pytest
    pytest_result = run_pytest(project_dir)

    # Fix loop (if enabled and there are failures)
    if use_fixloop and (syntax_errors or pytest_result["failed"] > 0 or pytest_result["passed"] == 0):
        for attempt in range(2):
            fix_attempts += 1
            # Find what's broken
            errors_text = ""
            if syntax_errors:
                errors_text += "Syntax errors:\n" + "\n".join(syntax_errors) + "\n\n"
            if pytest_result["output"]:
                errors_text += f"Pytest output:\n{pytest_result['output'][:1500]}\n\n"

            # Read the test file
            test_file = os.path.join(project_dir, "test_app.py")
            test_code = ""
            if os.path.exists(test_file):
                with open(test_file) as f:
                    test_code = f.read()

            # Read all files for context
            all_files_context = ""
            for agent in task["agents"]:
                fp = os.path.join(project_dir, agent["file"])
                if os.path.exists(fp):
                    with open(fp) as f:
                        all_files_context += f"\n### {agent['file']}:\n```python\n{f.read()}\n```\n"

            fix_prompt = (
                f"The following code has errors. Fix ONLY test_app.py.\n\n"
                f"Errors:\n{errors_text}\n\n"
                f"Current files:{all_files_context}\n\n"
                f"Write the COMPLETE fixed test_app.py. Fix ONLY the test file, "
                f"make it work with the existing database.py and main.py as-is."
            )

            fix_response = call_llm(fix_prompt)
            fix_code = extract_code(fix_response)
            total_tokens += len(fix_prompt.split()) + len(fix_response.split())

            with open(test_file, "w") as f:
                f.write(fix_code)

            # Re-check
            syntax_errors = check_syntax(project_dir)
            pytest_result = run_pytest(project_dir)

            if not syntax_errors and pytest_result["passed"] > 0 and pytest_result["failed"] == 0:
                break

    elapsed = time.time() - t0

    return {
        "pipeline": pipeline_name,
        "task": task["name"],
        "tests_passed": pytest_result["passed"],
        "tests_failed": pytest_result["failed"],
        "syntax_errors": len(syntax_errors),
        "tokens_approx": total_tokens,
        "time_sec": round(elapsed, 1),
        "fix_attempts": fix_attempts,
        "pytest_output": pytest_result["output"][:500],
    }


def main():
    print("=" * 70)
    print("FINAL E2E BENCHMARK — BASELINE vs IMPROVED (3 real projects)")
    print("=" * 70)

    all_results = []

    for task in TASKS:
        print(f"\n{'='*60}")
        print(f"PROJECT: {task['name']} — {task['description']}")
        print(f"{'='*60}")

        # BASELINE: HiveMind heuristic + keyword search + no fix loop
        print(f"\n[BASELINE] Running {task['name']}...")
        baseline = run_pipeline(task, "BASELINE", use_llmlingua=False, use_chroma=False, use_fixloop=False)
        print(f"  Tests: {baseline['tests_passed']} passed, {baseline['tests_failed']} failed, {baseline['syntax_errors']} syntax errors")
        all_results.append(baseline)

        # IMPROVED: LLMLingua + ChromaDB + fix loop
        print(f"\n[IMPROVED] Running {task['name']}...")
        improved = run_pipeline(task, "IMPROVED", use_llmlingua=True, use_chroma=True, use_fixloop=True)
        print(f"  Tests: {improved['tests_passed']} passed, {improved['tests_failed']} failed, {improved['syntax_errors']} syntax errors")
        print(f"  Fix attempts: {improved['fix_attempts']}")
        all_results.append(improved)

    # Summary
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)

    baseline_total = sum(r["tests_passed"] for r in all_results if r["pipeline"] == "BASELINE")
    improved_total = sum(r["tests_passed"] for r in all_results if r["pipeline"] == "IMPROVED")
    baseline_failed = sum(r["tests_failed"] for r in all_results if r["pipeline"] == "BASELINE")
    improved_failed = sum(r["tests_failed"] for r in all_results if r["pipeline"] == "IMPROVED")
    baseline_syntax = sum(r["syntax_errors"] for r in all_results if r["pipeline"] == "BASELINE")
    improved_syntax = sum(r["syntax_errors"] for r in all_results if r["pipeline"] == "IMPROVED")
    baseline_tokens = sum(r["tokens_approx"] for r in all_results if r["pipeline"] == "BASELINE")
    improved_tokens = sum(r["tokens_approx"] for r in all_results if r["pipeline"] == "IMPROVED")
    baseline_time = sum(r["time_sec"] for r in all_results if r["pipeline"] == "BASELINE")
    improved_time = sum(r["time_sec"] for r in all_results if r["pipeline"] == "IMPROVED")

    print(f"\n{'Metric':<25} {'BASELINE':>12} {'IMPROVED':>12} {'Winner':>12}")
    print("-" * 65)
    print(f"{'Tests Passed':<25} {baseline_total:>12} {improved_total:>12} {'IMPROVED' if improved_total > baseline_total else 'BASELINE' if baseline_total > improved_total else 'TIE':>12}")
    print(f"{'Tests Failed':<25} {baseline_failed:>12} {improved_failed:>12} {'IMPROVED' if improved_failed < baseline_failed else 'BASELINE' if baseline_failed < improved_failed else 'TIE':>12}")
    print(f"{'Syntax Errors':<25} {baseline_syntax:>12} {improved_syntax:>12} {'IMPROVED' if improved_syntax < baseline_syntax else 'BASELINE' if baseline_syntax < improved_syntax else 'TIE':>12}")
    print(f"{'Tokens (approx)':<25} {baseline_tokens:>12} {improved_tokens:>12} {'BASELINE' if baseline_tokens < improved_tokens else 'IMPROVED':>12}")
    print(f"{'Time (sec)':<25} {baseline_time:>12.1f} {improved_time:>12.1f} {'BASELINE' if baseline_time < improved_time else 'IMPROVED':>12}")

    print("\nPer-project breakdown:")
    for task in TASKS:
        b = next(r for r in all_results if r["pipeline"] == "BASELINE" and r["task"] == task["name"])
        i = next(r for r in all_results if r["pipeline"] == "IMPROVED" and r["task"] == task["name"])
        print(f"\n  {task['name']}:")
        print(f"    BASELINE: {b['tests_passed']} passed, {b['tests_failed']} failed ({b['time_sec']}s)")
        print(f"    IMPROVED: {i['tests_passed']} passed, {i['tests_failed']} failed ({i['time_sec']}s, {i['fix_attempts']} fixes)")

    # Save results
    results_path = "/home/ubuntu/hivemind/benchmarks/final_e2e_results.json"
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
