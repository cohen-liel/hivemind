#!/usr/bin/env python3
"""Hivemind Real Benchmark — runs actual projects through the system and measures results.

Usage:
    ./venv/bin/python benchmark/run_benchmark.py --variant baseline
    ./venv/bin/python benchmark/run_benchmark.py --variant enhanced-prompts
    ./venv/bin/python benchmark/run_benchmark.py --variant with-review

Requires: Hivemind server running on localhost:8090
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

BASE_URL = os.getenv("HIVEMIND_URL", "http://localhost:8090")
PROJECTS_ROOT = Path.home() / "claude-projects"
RESULTS_DIR = Path(__file__).parent / "results"

# ── Test Projects ──────────────────────────────────────────────────────────

PROJECTS = [
    {
        "id": "bench-calculator",
        "name": "bench-calculator",
        "description": "CLI calculator with basic math operations",
        "prompt": (
            "Build a Python CLI calculator app. Requirements:\n"
            "1. Support +, -, *, / operations\n"
            "2. Handle division by zero gracefully\n"
            "3. Support chained expressions like '2 + 3 * 4'\n"
            "4. Add a REPL mode (interactive prompt)\n"
            "5. Write pytest tests with at least 10 test cases\n"
            "6. Create a requirements.txt and README.md"
        ),
        "complexity": "simple",
    },
    {
        "id": "bench-todo-api",
        "name": "bench-todo-api",
        "description": "REST API for TODO management",
        "prompt": (
            "Build a Python REST API for TODO management using FastAPI. Requirements:\n"
            "1. CRUD endpoints: POST /todos, GET /todos, GET /todos/{id}, PUT /todos/{id}, DELETE /todos/{id}\n"
            "2. Todo model: id, title, description, completed (bool), created_at\n"
            "3. In-memory storage (list/dict, no database needed)\n"
            "4. Input validation with Pydantic\n"
            "5. Proper HTTP status codes (201, 404, etc.)\n"
            "6. Write pytest tests for all endpoints using httpx TestClient\n"
            "7. Create requirements.txt and README.md"
        ),
        "complexity": "medium",
    },
    {
        "id": "bench-url-shortener",
        "name": "bench-url-shortener",
        "description": "URL shortener with click tracking",
        "prompt": (
            "Build a Python URL shortener service using FastAPI. Requirements:\n"
            "1. POST /shorten — accepts {url: string}, returns {short_code: string, short_url: string}\n"
            "2. GET /{short_code} — redirects (302) to the original URL\n"
            "3. GET /stats/{short_code} — returns click count, created_at, original_url\n"
            "4. Generate unique 6-character alphanumeric codes\n"
            "5. Validate URLs (must be valid http/https)\n"
            "6. In-memory storage (dict)\n"
            "7. Handle duplicate URLs (return existing short code)\n"
            "8. Rate limiting: max 10 shortens per minute per IP\n"
            "9. Write comprehensive pytest tests (at least 15 test cases)\n"
            "10. Create requirements.txt and README.md"
        ),
        "complexity": "complex",
    },
]


class BenchmarkRunner:
    def __init__(self, variant: str, device_token: str):
        self.variant = variant
        self.token = device_token
        self.results = {}
        self.variant_dir = RESULTS_DIR / variant / time.strftime("%Y%m%d_%H%M%S")
        self.variant_dir.mkdir(parents=True, exist_ok=True)

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "X-Device-Token": self.token,
        }

    def _api(self, method, path, **kwargs):
        url = f"{BASE_URL}{path}"
        resp = getattr(requests, method)(url, headers=self._headers(), **kwargs)
        return resp.json()

    def create_project(self, project: dict) -> str | None:
        """Create a project, cleaning up any existing one first. Returns actual project_id."""
        pid = project["id"]
        proj_dir = PROJECTS_ROOT / pid

        # Clean up project directory
        if proj_dir.exists():
            shutil.rmtree(proj_dir)
        proj_dir.mkdir(parents=True, exist_ok=True)

        # Try to delete existing project
        self._api("delete", f"/api/projects/{pid}")

        # Create new
        resp = self._api("post", "/api/projects", json={
            "name": project["name"],
            "directory": str(proj_dir),
            "description": project["description"],
        })
        if resp.get("ok"):
            actual_id = resp.get("project_id", pid)
            print(f"  Created project: {actual_id}")
            return actual_id
        print(f"  Failed to create project {pid}: {resp}")
        return None

    def send_prompt(self, project_id: str, prompt: str) -> dict:
        """Send a build prompt to a project and return the response."""
        resp = self._api("post", f"/api/projects/{project_id}/message", json={
            "message": prompt,
        })
        return resp

    def wait_for_completion(self, project_id: str, timeout: int = 600) -> dict:
        """Poll project and queue status until truly done or timeout."""
        start = time.time()
        last_log = ""
        idle_since = None  # Track when we first saw idle + empty queue

        while time.time() - start < timeout:
            try:
                resp = self._api("get", f"/api/projects/{project_id}")
                queue = self._api("get", f"/api/projects/{project_id}/queue")

                status = resp.get("status", "unknown")
                running = queue.get("running_count", 0)
                pending = queue.get("queue_depth", 0)
                active_tasks = queue.get("active_tasks", [])
                current_agent = resp.get("current_agent", "")
                dag = resp.get("dag_progress")

                # Build log line
                elapsed = int(time.time() - start)
                log_line = f"status={status} agents_running={running} pending={pending}"
                if current_agent:
                    log_line += f" agent={current_agent}"
                if dag:
                    done = dag.get("completed_tasks", 0)
                    total = dag.get("total_tasks", 0)
                    log_line += f" dag={done}/{total}"
                if active_tasks:
                    task_elapsed = active_tasks[0].get("elapsed_seconds", 0)
                    log_line += f" task_time={task_elapsed:.0f}s"

                if log_line != last_log:
                    print(f"    [{elapsed}s] {log_line}")
                    last_log = log_line

                # Done when: status is not running AND no tasks in queue
                is_done = status in ("idle", "completed", "error", "stopped")
                queue_empty = running == 0 and pending == 0

                if is_done and queue_empty:
                    if idle_since is None:
                        idle_since = time.time()
                    # Wait 5s after going idle to catch late status updates
                    if time.time() - idle_since > 5:
                        return {
                            "status": status,
                            "elapsed": time.time() - start,
                            "turn_count": resp.get("turn_count", 0),
                            "cost_usd": resp.get("total_cost_usd", 0),
                            "tokens": {
                                "input": resp.get("total_input_tokens", 0),
                                "output": resp.get("total_output_tokens", 0),
                                "total": resp.get("total_tokens", 0),
                            },
                            "agent_states": resp.get("agent_states", {}),
                            "dag_progress": dag,
                        }
                else:
                    idle_since = None

            except Exception as e:
                print(f"    Poll error: {e}")

            time.sleep(10)

        return {"status": "timeout", "elapsed": timeout}

    def evaluate_project(self, project: dict) -> dict:
        """Evaluate the built project — check files, run tests."""
        pid = project["id"]
        proj_dir = PROJECTS_ROOT / pid
        result = {
            "project_id": pid,
            "complexity": project["complexity"],
            "files_created": [],
            "total_lines": 0,
            "has_tests": False,
            "test_pass_count": 0,
            "test_fail_count": 0,
            "test_error": None,
            "has_readme": False,
            "has_requirements": False,
        }

        # Count files
        for f in proj_dir.rglob("*"):
            if f.is_file() and not f.name.startswith(".") and "__pycache__" not in str(f):
                rel = str(f.relative_to(proj_dir))
                result["files_created"].append(rel)
                try:
                    result["total_lines"] += len(f.read_text().splitlines())
                except Exception:
                    pass

        result["has_readme"] = any("readme" in f.lower() for f in result["files_created"])
        result["has_requirements"] = any("requirements" in f.lower() for f in result["files_created"])
        result["has_tests"] = any("test" in f.lower() for f in result["files_created"])

        # Run tests
        test_files = [f for f in result["files_created"] if "test" in f.lower() and f.endswith(".py")]
        if test_files:
            try:
                # Install deps first
                req_file = proj_dir / "requirements.txt"
                if req_file.exists():
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", "-q",
                         "--trusted-host", "pypi.org",
                         "--trusted-host", "files.pythonhosted.org",
                         "-r", str(req_file)],
                        capture_output=True, timeout=60,
                    )

                # Run pytest
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", str(proj_dir), "-v", "--tb=short", "--no-header"],
                    capture_output=True, text=True, timeout=120,
                    cwd=str(proj_dir),
                )
                output = proc.stdout + proc.stderr

                # Parse results
                for line in output.splitlines():
                    if " passed" in line:
                        import re
                        m = re.search(r"(\d+) passed", line)
                        if m:
                            result["test_pass_count"] = int(m.group(1))
                    if " failed" in line:
                        import re
                        m = re.search(r"(\d+) failed", line)
                        if m:
                            result["test_fail_count"] = int(m.group(1))

                result["test_output"] = output[-2000:]  # last 2k chars
            except subprocess.TimeoutExpired:
                result["test_error"] = "timeout"
            except Exception as e:
                result["test_error"] = str(e)

        return result

    def run_project(self, project: dict) -> dict:
        """Run a single project end-to-end."""
        pid = project["id"]
        print(f"\n{'='*60}")
        print(f"Project: {pid} ({project['complexity']})")
        print(f"{'='*60}")

        # Create
        actual_id = self.create_project(project)
        if not actual_id:
            return {"error": "Failed to create project"}
        pid = actual_id  # Use the actual project_id returned by API

        # Send prompt
        print(f"  Sending prompt...")
        start_time = time.time()
        send_resp = self.send_prompt(pid, project["prompt"])
        print(f"  Send response: {json.dumps(send_resp)[:200]}")

        # Wait
        print(f"  Waiting for completion (max 10 min)...")
        completion = self.wait_for_completion(pid, timeout=600)
        total_time = time.time() - start_time

        # Evaluate
        print(f"  Evaluating output...")
        evaluation = self.evaluate_project(project)

        result = {
            "project": pid,
            "variant": self.variant,
            "complexity": project["complexity"],
            "completion": completion,
            "evaluation": evaluation,
            "total_time_seconds": round(total_time, 1),
        }

        # Save individual result
        result_file = self.variant_dir / f"{pid}.json"
        result_file.write_text(json.dumps(result, indent=2, default=str))
        print(f"  Result saved: {result_file}")

        return result

    def run_all(self):
        """Run all benchmark projects."""
        print(f"\n{'#'*60}")
        print(f"  HIVEMIND BENCHMARK — Variant: {self.variant}")
        print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'#'*60}")

        all_results = []
        for project in PROJECTS:
            result = self.run_project(project)
            all_results.append(result)
            self.results[project["id"]] = result

        # Save summary
        summary = {
            "variant": self.variant,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "projects": all_results,
            "totals": {
                "total_time": sum(r.get("total_time_seconds", 0) for r in all_results),
                "total_tests_passed": sum(
                    r.get("evaluation", {}).get("test_pass_count", 0) for r in all_results
                ),
                "total_tests_failed": sum(
                    r.get("evaluation", {}).get("test_fail_count", 0) for r in all_results
                ),
                "total_files": sum(
                    len(r.get("evaluation", {}).get("files_created", [])) for r in all_results
                ),
                "total_lines": sum(
                    r.get("evaluation", {}).get("total_lines", 0) for r in all_results
                ),
            },
        }

        summary_file = self.variant_dir / "summary.json"
        summary_file.write_text(json.dumps(summary, indent=2, default=str))
        print(f"\n{'='*60}")
        print(f"SUMMARY saved: {summary_file}")
        print(f"{'='*60}")
        self._print_summary(summary)
        return summary

    def _print_summary(self, summary: dict):
        totals = summary["totals"]
        print(f"\nVariant: {summary['variant']}")
        print(f"Total time: {totals['total_time']:.0f}s")
        print(f"Tests passed: {totals['total_tests_passed']}")
        print(f"Tests failed: {totals['total_tests_failed']}")
        print(f"Files created: {totals['total_files']}")
        print(f"Lines of code: {totals['total_lines']}")
        print()
        for proj in summary["projects"]:
            ev = proj.get("evaluation", {})
            comp = proj.get("completion", {})
            print(f"  {proj['project']:25s} | "
                  f"status={comp.get('status', '?'):10s} | "
                  f"tests={ev.get('test_pass_count', 0)}/{ev.get('test_pass_count', 0) + ev.get('test_fail_count', 0)} | "
                  f"files={len(ev.get('files_created', []))} | "
                  f"time={proj.get('total_time_seconds', 0):.0f}s")


def get_device_token():
    """Get or create a device token."""
    # Try to read from env
    token = os.getenv("DEVICE_TOKEN", "")
    if token:
        return token

    # Try to get from access code in logs
    try:
        log = Path("/tmp/hivemind.log").read_bytes()
        import re
        codes = re.findall(rb"ACCESS CODE:\s+(\w+)", log)
        if codes:
            code = codes[-1].decode()
            resp = requests.post(f"{BASE_URL}/api/auth/verify",
                                 json={"code": code},
                                 headers={"Content-Type": "application/json"})
            data = resp.json()
            if data.get("device_token"):
                return data["device_token"]
    except Exception:
        pass

    print("ERROR: Set DEVICE_TOKEN env var or ensure server is running with visible logs")
    sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hivemind Real Benchmark")
    parser.add_argument("--variant", default="baseline", help="Variant name (baseline, enhanced-prompts, with-review)")
    parser.add_argument("--project", help="Run only a specific project (bench-calculator, bench-todo-api, bench-url-shortener)")
    args = parser.parse_args()

    token = get_device_token()
    runner = BenchmarkRunner(args.variant, token)

    if args.project:
        proj = next((p for p in PROJECTS if p["id"] == args.project), None)
        if not proj:
            print(f"Unknown project: {args.project}")
            sys.exit(1)
        runner.run_project(proj)
    else:
        runner.run_all()
