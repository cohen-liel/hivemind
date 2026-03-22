"""Real E2E Benchmark — generates actual code, writes to disk, runs it.

NO LLM-as-judge. Real metrics only:
1. Does the code compile? (py_compile)
2. Does `python -c "from main import app"` work?
3. Does the server respond to HTTP requests?
4. How many pytest tests pass/fail/error?

Runs two pipelines on the SAME project spec:
- OLD: plain prompts, aggressively truncated context (simulates heuristic compressor)
- NEW: enhanced prompts (output schemas + few-shot + downstream contracts) + full context

Project: Agent-to-Agent Chat System (FastAPI + SQLite)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI

client = OpenAI()
MODEL = "gpt-4.1-mini"

# ── Project Specification ────────────────────────────────────────────────

PROJECT_SPEC = """Build an Agent-to-Agent Chat System with the following requirements:

1. **Database Layer** (SQLite):
   - agents table: id (INTEGER PRIMARY KEY AUTOINCREMENT), name (TEXT UNIQUE NOT NULL), role (TEXT NOT NULL), created_at (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
   - messages table: id (INTEGER PRIMARY KEY AUTOINCREMENT), sender_id (INTEGER REFERENCES agents(id)), receiver_id (INTEGER REFERENCES agents(id)), content (TEXT NOT NULL), timestamp (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
   - Database init function that creates tables if not exist
   - Use sqlite3 standard library (no ORM)

2. **Backend API** (FastAPI):
   - POST /agents — register a new agent (body: {"name": str, "role": str}) → returns agent dict with id
   - GET /agents — list all agents → returns list of agent dicts
   - GET /agents/{agent_id} — get single agent → returns agent dict or 404
   - POST /messages — send message (body: {"sender_id": int, "receiver_id": int, "content": str}) → returns message dict
   - GET /messages?sender_id=X&receiver_id=Y — get conversation → returns list of message dicts
   - GET /agents/{agent_id}/messages — all messages for agent → returns list of message dicts
   - Use Pydantic BaseModel for request/response schemas
   - Return proper HTTP status codes (201 for creation, 404 for not found, 400 for bad request)

3. **Tests** (pytest with FastAPI TestClient):
   - Test agent creation (POST /agents with valid data → 201)
   - Test duplicate agent name (POST /agents with same name → 400 or 409)
   - Test list agents (GET /agents → 200 + list)
   - Test get single agent (GET /agents/1 → 200, GET /agents/999 → 404)
   - Test send message (POST /messages → 201)
   - Test send message to non-existent agent (→ 400 or 404)
   - Test get conversation (GET /messages?sender_id=1&receiver_id=2 → 200)
   - Test get agent messages (GET /agents/1/messages → 200)

ALL files must be in the project root directory:
- database.py — SQLite database layer with init_db() and query functions
- models.py — Pydantic models for request/response
- main.py — FastAPI app with all endpoints (imports from database.py and models.py)
- test_app.py — pytest tests using TestClient (imports from main.py)
"""

# ── Agent DAG ────────────────────────────────────────────────────────────

AGENTS = [
    {
        "role": "database_expert",
        "task": (
            "Create database.py for the Agent-to-Agent Chat System.\n"
            "It must contain:\n"
            "- init_db() function that creates the agents and messages tables\n"
            "- create_agent(name, role) → returns the new agent as a dict\n"
            "- get_agents() → returns list of all agents as dicts\n"
            "- get_agent(agent_id) → returns agent dict or None\n"
            "- create_message(sender_id, receiver_id, content) → returns message dict\n"
            "- get_conversation(sender_id, receiver_id) → returns list of message dicts\n"
            "- get_agent_messages(agent_id) → returns list of message dicts\n"
            "Use sqlite3 standard library. Database file: 'chat.db'.\n"
            "Output ONLY database.py in a ```python block starting with # database.py"
        ),
        "files_expected": ["database.py"],
        "downstream": ["backend_developer"],
    },
    {
        "role": "backend_developer",
        "task": (
            "Create models.py and main.py for the Agent-to-Agent Chat System.\n"
            "models.py must contain Pydantic BaseModel classes for requests and responses.\n"
            "main.py must contain a FastAPI app with all endpoints.\n"
            "main.py must import from database.py and models.py.\n"
            "main.py must call init_db() at startup (use @app.on_event('startup') or lifespan).\n"
            "Output BOTH files in separate ```python blocks, each starting with # filename.py"
        ),
        "files_expected": ["models.py", "main.py"],
        "downstream": ["test_engineer"],
        "depends_on": ["database_expert"],
    },
    {
        "role": "test_engineer",
        "task": (
            "Create test_app.py with comprehensive pytest tests.\n"
            "Use FastAPI TestClient: from fastapi.testclient import TestClient\n"
            "Import the app: from main import app\n"
            "Create a fresh database for each test (use a fixture that calls init_db()).\n"
            "Test ALL endpoints including error cases.\n"
            "Output ONLY test_app.py in a ```python block starting with # test_app.py"
        ),
        "files_expected": ["test_app.py"],
        "downstream": [],
        "depends_on": ["database_expert", "backend_developer"],
    },
]


# ── File Extraction ──────────────────────────────────────────────────────

def extract_files(llm_output: str) -> dict[str, str]:
    """Extract Python files from LLM output — robust multi-strategy extraction."""
    files = {}

    # Find all code blocks
    pattern = r'```(?:python)?\s*\n(.*?)```'
    blocks = re.findall(pattern, llm_output, re.DOTALL)

    for block in blocks:
        lines = block.strip().split('\n')
        if not lines:
            continue

        filename = None
        code_start = 0

        # Strategy 1: First line is # filename.py
        first = lines[0].strip()
        if re.match(r'^#\s*\w+\.py', first):
            match = re.search(r'(\w+\.py)', first)
            if match:
                filename = match.group(1)
                code_start = 1

        # Strategy 2: Look for filename in text before code block
        if not filename:
            idx = llm_output.find(block)
            if idx > 0:
                before = llm_output[max(0, idx - 300):idx]
                # Look for **filename.py**, `filename.py`, ### filename.py, etc.
                matches = re.findall(r'[`*#]*\s*(\w+\.py)\s*[`*]*', before)
                if matches:
                    filename = matches[-1]  # Take the closest one

        # Strategy 3: Infer from content
        if not filename:
            code = '\n'.join(lines)
            if 'FastAPI' in code and 'app = ' in code:
                filename = 'main.py'
            elif 'BaseModel' in code and 'class ' in code and 'FastAPI' not in code:
                filename = 'models.py'
            elif 'def test_' in code or 'TestClient' in code:
                filename = 'test_app.py'
            elif 'CREATE TABLE' in code or 'sqlite3' in code:
                filename = 'database.py'

        if filename:
            code = '\n'.join(lines[code_start:]).strip()
            if code:
                files[filename] = code

    return files


# ── Data Classes ─────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    role: str
    output: str
    files_written: list[str] = field(default_factory=list)
    elapsed: float = 0
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class RealTestResults:
    """Hard metrics — no LLM judging."""
    files_on_disk: list[str] = field(default_factory=list)
    syntax_errors: list[str] = field(default_factory=list)
    import_ok: bool = False
    import_error: str = ""
    server_responds: bool = False
    server_response_code: int = 0
    server_response_body: str = ""
    pytest_passed: int = 0
    pytest_failed: int = 0
    pytest_errors: int = 0
    pytest_total: int = 0
    pytest_output: str = ""
    runtime_errors: list[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    name: str
    agents: list[AgentResult] = field(default_factory=list)
    total_time: float = 0
    total_tokens: int = 0
    tests: RealTestResults = field(default_factory=RealTestResults)


# ── Pipeline Runner ──────────────────────────────────────────────────────

def run_pipeline(name: str, project_dir: Path, use_enhancements: bool) -> PipelineResult:
    """Run the agent DAG, writing real files to disk."""

    result = PipelineResult(name=name)

    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True)

    agent_outputs: dict[str, str] = {}  # role -> raw output
    t_start = time.time()

    for spec in AGENTS:
        role = spec["role"]
        task = spec["task"]
        depends_on = spec.get("depends_on", [])
        downstream = spec.get("downstream", [])

        # ── Build prompt ──
        prompt_parts = [
            f"You are a senior {role.replace('_', ' ')}.",
            f"\n## Project Specification\n{PROJECT_SPEC}",
            f"\n## Your Task\n{task}",
        ]

        # Add upstream context (code from previous agents)
        if depends_on:
            prompt_parts.append("\n## Code From Previous Agents")
            for dep in depends_on:
                if dep in agent_outputs:
                    dep_output = agent_outputs[dep]
                    if not use_enhancements:
                        # OLD pipeline: aggressive truncation — only keep first 400 chars
                        dep_output = dep_output[:400] + "\n... [TRUNCATED]"
                    prompt_parts.append(f"\n### {dep} output:\n{dep_output}")

            # Also include actual files on disk so agent sees real code
            prompt_parts.append("\n## Actual Files Already Created")
            for pyfile in sorted(project_dir.glob("*.py")):
                content = pyfile.read_text()
                if not use_enhancements:
                    # OLD: truncate file content
                    content = content[:500] + ("\n# ... [TRUNCATED]" if len(content) > 500 else "")
                prompt_parts.append(f"\n### {pyfile.name}\n```python\n{content}\n```")

        # NEW pipeline: add enhancements
        if use_enhancements:
            from prompt_enhancer import build_enhancement, inject_enhancements
            enhancement = build_enhancement(
                role=role,
                downstream_roles=downstream,
                acceptance_criteria=[f"Create {f}" for f in spec["files_expected"]],
            )
            prompt_parts.append(inject_enhancements("", enhancement))

        prompt = "\n".join(prompt_parts)

        # ── Call LLM ──
        print(f"  [{role}] calling LLM...", end=" ", flush=True)
        t0 = time.time()
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.1,
        )
        elapsed = time.time() - t0
        output = resp.choices[0].message.content or ""
        tok_in = resp.usage.prompt_tokens
        tok_out = resp.usage.completion_tokens

        # ── Extract and write files ──
        files = extract_files(output)
        written = []
        for fname, code in files.items():
            (project_dir / fname).write_text(code + "\n")
            written.append(fname)

        # Check if expected files were written
        for expected in spec["files_expected"]:
            if expected not in written:
                print(f"WARNING: {expected} not extracted!", end=" ")

        agent_outputs[role] = output
        result.agents.append(AgentResult(
            role=role, output=output, files_written=written,
            elapsed=elapsed, tokens_in=tok_in, tokens_out=tok_out,
        ))
        print(f"{elapsed:.1f}s, {tok_in+tok_out} tok, files: {written}")

    result.total_time = time.time() - t_start
    result.total_tokens = sum(a.tokens_in + a.tokens_out for a in result.agents)
    return result


# ── Real Testing ─────────────────────────────────────────────────────────

def run_real_tests(project_dir: Path, result: PipelineResult) -> None:
    """Run REAL tests — compile, import, HTTP, pytest. No LLM judging."""

    t = result.tests
    t.files_on_disk = sorted([f.name for f in project_dir.glob("*.py")])
    print(f"\n  Files on disk: {t.files_on_disk}")

    # ── 1. Syntax check ──
    print("  [1/4] Syntax check (py_compile)...")
    for pyfile in project_dir.glob("*.py"):
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(pyfile)],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            err = proc.stderr.strip()[:200]
            t.syntax_errors.append(f"{pyfile.name}: {err}")
            print(f"    ✗ {pyfile.name}: {err}")
        else:
            print(f"    ✓ {pyfile.name}")

    # ── 2. Import check ──
    print("  [2/4] Import check (from main import app)...")
    if (project_dir / "main.py").exists():
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '.'); from main import app; print('OK')"],
            capture_output=True, text=True, timeout=15,
            cwd=str(project_dir),
        )
        if proc.returncode == 0 and "OK" in proc.stdout:
            t.import_ok = True
            print("    ✓ import successful")
        else:
            t.import_error = (proc.stderr.strip() + proc.stdout.strip())[:300]
            t.runtime_errors.append(f"import failed: {t.import_error}")
            print(f"    ✗ {t.import_error}")
    else:
        t.runtime_errors.append("main.py not found")
        print("    ✗ main.py not found")

    # ── 3. Server responds ──
    print("  [3/4] HTTP check (GET /agents)...")
    if t.import_ok:
        proc = subprocess.run(
            [sys.executable, "-c", """
import sys; sys.path.insert(0, '.')
from main import app
from fastapi.testclient import TestClient
c = TestClient(app)
r = c.get('/agents')
print(f'STATUS:{r.status_code}')
print(f'BODY:{r.text[:300]}')
"""],
            capture_output=True, text=True, timeout=15,
            cwd=str(project_dir),
        )
        output = proc.stdout + proc.stderr
        status_match = re.search(r'STATUS:(\d+)', output)
        if status_match:
            t.server_responds = True
            t.server_response_code = int(status_match.group(1))
            body_match = re.search(r'BODY:(.*)', output, re.DOTALL)
            t.server_response_body = body_match.group(1).strip()[:200] if body_match else ""
            print(f"    ✓ Status {t.server_response_code}: {t.server_response_body[:100]}")
        else:
            err = output.strip()[:300]
            t.runtime_errors.append(f"server error: {err}")
            print(f"    ✗ {err}")
    else:
        print("    ⊘ skipped (import failed)")

    # ── 4. Pytest ──
    print("  [4/4] Running pytest...")
    if (project_dir / "test_app.py").exists():
        # Remove any leftover database
        for db in project_dir.glob("*.db"):
            db.unlink()

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "test_app.py", "-v", "--tb=short", "-x"],
            capture_output=True, text=True, timeout=60,
            cwd=str(project_dir),
            env={**os.environ, "PYTHONPATH": str(project_dir)},
        )
        t.pytest_output = proc.stdout + proc.stderr

        # Parse results
        for line in t.pytest_output.split('\n'):
            m = re.search(r'(\d+) passed', line)
            if m: t.pytest_passed = int(m.group(1))
            m = re.search(r'(\d+) failed', line)
            if m: t.pytest_failed = int(m.group(1))
            m = re.search(r'(\d+) error', line)
            if m: t.pytest_errors = int(m.group(1))

        t.pytest_total = t.pytest_passed + t.pytest_failed + t.pytest_errors
        print(f"    Results: {t.pytest_passed} passed, {t.pytest_failed} failed, {t.pytest_errors} errors (total: {t.pytest_total})")

        # Show failures
        if t.pytest_failed > 0 or t.pytest_errors > 0:
            for line in t.pytest_output.split('\n'):
                if 'FAILED' in line or 'ERROR' in line:
                    print(f"      {line.strip()}")
    else:
        t.runtime_errors.append("test_app.py not found")
        print("    ✗ test_app.py not found")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("REAL E2E BENCHMARK")
    print("Code is written to disk. Code is executed. No LLM judging.")
    print("=" * 70)
    print(f"Model: {MODEL}")
    print(f"Project: Agent-to-Agent Chat System (FastAPI + SQLite)")
    print(f"Agents: {' → '.join(a['role'] for a in AGENTS)}")

    base = Path(__file__).parent

    # ── OLD Pipeline ──
    print(f"\n{'━' * 70}")
    print("▶ OLD PIPELINE (plain prompts, truncated context)")
    print(f"{'━' * 70}")
    old_dir = base / "real_project_OLD"
    old = run_pipeline("OLD", old_dir, use_enhancements=False)
    run_real_tests(old_dir, old)

    # ── NEW Pipeline ──
    print(f"\n{'━' * 70}")
    print("▶ NEW PIPELINE (enhanced prompts + full context)")
    print(f"{'━' * 70}")
    new_dir = base / "real_project_NEW"
    new = run_pipeline("NEW", new_dir, use_enhancements=True)
    run_real_tests(new_dir, new)

    # ── Comparison ──
    print(f"\n{'━' * 70}")
    print("RESULTS — REAL METRICS")
    print(f"{'━' * 70}")

    ot = old.tests
    nt = new.tests

    rows = [
        ("Files generated",       str(ot.files_on_disk),    str(nt.files_on_disk)),
        ("Syntax errors",         str(len(ot.syntax_errors)), str(len(nt.syntax_errors))),
        ("Import OK",             "✓" if ot.import_ok else "✗", "✓" if nt.import_ok else "✗"),
        ("Server responds",       "✓" if ot.server_responds else "✗", "✓" if nt.server_responds else "✗"),
        ("HTTP status",           str(ot.server_response_code), str(nt.server_response_code)),
        ("Pytest passed",         str(ot.pytest_passed),    str(nt.pytest_passed)),
        ("Pytest failed",         str(ot.pytest_failed),    str(nt.pytest_failed)),
        ("Pytest errors",         str(ot.pytest_errors),    str(nt.pytest_errors)),
        ("Pytest total",          str(ot.pytest_total),     str(nt.pytest_total)),
        ("Runtime errors",        str(len(ot.runtime_errors)), str(len(nt.runtime_errors))),
        ("Total time (s)",        f"{old.total_time:.1f}",  f"{new.total_time:.1f}"),
        ("Total tokens",          str(old.total_tokens),    str(new.total_tokens)),
    ]

    print(f"\n  {'Metric':<25} {'OLD':>20} {'NEW':>20}")
    print(f"  {'─' * 65}")
    for label, oval, nval in rows:
        print(f"  {label:<25} {oval:>20} {nval:>20}")

    # ── Save everything ──
    results = {
        "model": MODEL,
        "project": "Agent-to-Agent Chat System",
        "old": {
            "files": ot.files_on_disk,
            "syntax_errors": ot.syntax_errors,
            "import_ok": ot.import_ok,
            "import_error": ot.import_error,
            "server_responds": ot.server_responds,
            "server_response_code": ot.server_response_code,
            "pytest_passed": ot.pytest_passed,
            "pytest_failed": ot.pytest_failed,
            "pytest_errors": ot.pytest_errors,
            "pytest_total": ot.pytest_total,
            "runtime_errors": ot.runtime_errors,
            "total_time": old.total_time,
            "total_tokens": old.total_tokens,
        },
        "new": {
            "files": nt.files_on_disk,
            "syntax_errors": nt.syntax_errors,
            "import_ok": nt.import_ok,
            "import_error": nt.import_error,
            "server_responds": nt.server_responds,
            "server_response_code": nt.server_response_code,
            "pytest_passed": nt.pytest_passed,
            "pytest_failed": nt.pytest_failed,
            "pytest_errors": nt.pytest_errors,
            "pytest_total": nt.pytest_total,
            "runtime_errors": nt.runtime_errors,
            "total_time": new.total_time,
            "total_tokens": new.total_tokens,
        },
    }

    (base / "real_e2e_results.json").write_text(json.dumps(results, indent=2, default=str))
    (base / "real_pytest_OLD.txt").write_text(ot.pytest_output)
    (base / "real_pytest_NEW.txt").write_text(nt.pytest_output)

    print(f"\n  Results saved to benchmarks/real_e2e_results.json")
    print(f"  Pytest outputs: real_pytest_OLD.txt, real_pytest_NEW.txt")
    print(f"  Generated projects: real_project_OLD/, real_project_NEW/")


if __name__ == "__main__":
    main()
