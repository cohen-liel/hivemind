"""
End-to-End Project Simulation Benchmark
========================================
Simulates a full HiveMind project build using two pipelines:
  - OLD: heuristic compression + JSON memory + static model (always strong)
  - NEW: LLMLingua compression + ChromaDB memory + smart routing

Both pipelines use the same LLM (gpt-4.1-mini via OpenAI-compatible API)
to generate actual code, then an LLM-as-judge reviews the outputs.

Project: "TaskFlow" — a task management REST API with:
  Layer 1: PM breaks down requirements
  Layer 2: Architect designs system
  Layer 3: Backend builds API, Frontend builds UI (parallel)
  Layer 4: Tester writes tests, DevOps sets up CI/CD (parallel)
  Layer 5: Code reviewer reviews everything

Metrics tracked:
  - Wall-clock time per agent and total
  - Token usage (input + output) per agent and total
  - Estimated cost per pipeline
  - Code quality score (LLM-as-judge)
  - Memory utilization (did the agent use cross-project lessons?)
  - Context quality (did compressed context lose critical info?)
"""

import json
import os
import sys
import time
import tempfile
import shutil
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI

client = OpenAI()

# ── Project Specification ────────────────────────────────────────────────

PROJECT_SPEC = """Build "TaskFlow" — a task management REST API.

Requirements:
1. REST API with FastAPI (Python):
   - POST /tasks — Create a task (title, description, priority, due_date)
   - GET /tasks — List tasks with filtering (status, priority) and pagination
   - GET /tasks/{id} — Get task details
   - PUT /tasks/{id} — Update task
   - DELETE /tasks/{id} — Delete task
   - POST /tasks/{id}/complete — Mark task as complete
2. SQLite database with SQLAlchemy ORM
3. Input validation with Pydantic models
4. Error handling with proper HTTP status codes
5. Unit tests with pytest (>80% coverage target)
6. Dockerfile for containerization
7. GitHub Actions CI pipeline
"""

# ── DAG Definition (mirrors HiveMind's real DAG structure) ───────────────

DAG_LAYERS = [
    {
        "layer": 1,
        "agents": [
            {
                "role": "pm",
                "prompt": f"You are a Project Manager. Break down this project into specific technical tasks for the team:\n\n{PROJECT_SPEC}\n\nOutput a structured task list with assignments to: architect, backend_dev, frontend_dev, tester, devops.",
            }
        ],
    },
    {
        "layer": 2,
        "agents": [
            {
                "role": "architect",
                "prompt": "You are a Software Architect. Based on the PM's task breakdown, design the system architecture for TaskFlow. Include: file structure, database schema, API contracts, and technology decisions. Output concrete technical specifications.",
            }
        ],
    },
    {
        "layer": 3,
        "agents": [
            {
                "role": "backend_dev",
                "prompt": "You are a Backend Developer. Based on the architect's design, implement the complete FastAPI application. Output the full source code for: main.py, models.py, schemas.py, database.py, and requirements.txt. The code must be production-ready and runnable.",
            },
            {
                "role": "frontend_dev",
                "prompt": "You are a Frontend Developer. Create a simple CLI client (cli_client.py) that interacts with the TaskFlow API. Include commands for: create, list, get, update, delete, and complete tasks. Use the requests library.",
            },
        ],
    },
    {
        "layer": 4,
        "agents": [
            {
                "role": "tester",
                "prompt": "You are a QA Engineer. Write comprehensive pytest tests for the TaskFlow API. Include: unit tests for models, integration tests for endpoints, edge case tests (invalid input, not found, etc.). Target >80% coverage. Output the full test_main.py file.",
            },
            {
                "role": "devops",
                "prompt": "You are a DevOps Engineer. Create: 1) A Dockerfile for the TaskFlow API, 2) A docker-compose.yml, 3) A GitHub Actions CI pipeline (.github/workflows/ci.yml) that runs tests and builds the Docker image. Output all files.",
            },
        ],
    },
    {
        "layer": 5,
        "agents": [
            {
                "role": "code_reviewer",
                "prompt": "You are a Senior Code Reviewer. Review ALL the code produced by the team. Check for: bugs, security issues, performance problems, code style, missing error handling, and best practices. Provide specific feedback with line references and suggested fixes.",
            }
        ],
    },
]

# ── Cross-project lessons (pre-loaded memory) ───────────────────────────

PAST_LESSONS = [
    {"project_id": "proj_prev_1", "category": "configuration", "lesson": "FastAPI with SQLite needs 'check_same_thread=False' in create_engine to avoid threading errors in production", "tech_stack": ["fastapi", "sqlite", "sqlalchemy"], "severity": "critical"},
    {"project_id": "proj_prev_1", "category": "failure", "lesson": "Pydantic V2 uses model_validator instead of validator — always check Pydantic version compatibility", "tech_stack": ["pydantic", "fastapi"], "severity": "warning"},
    {"project_id": "proj_prev_2", "category": "testing", "lesson": "Use TestClient from fastapi.testclient for integration tests — it handles async properly without needing httpx", "tech_stack": ["fastapi", "pytest", "testing"], "severity": "info"},
    {"project_id": "proj_prev_2", "category": "security", "lesson": "Always validate and sanitize task IDs as integers to prevent SQL injection — use Path(gt=0) in FastAPI", "tech_stack": ["fastapi", "security", "sqlalchemy"], "severity": "critical"},
    {"project_id": "proj_prev_3", "category": "docker", "lesson": "Use multi-stage Docker builds for Python apps — builder stage installs deps, runtime stage copies only site-packages to reduce image size by 60%", "tech_stack": ["docker", "python", "optimization"], "severity": "info"},
    {"project_id": "proj_prev_3", "category": "ci", "lesson": "GitHub Actions should cache pip dependencies using actions/cache with hashFiles('**/requirements.txt') as key to speed up CI by 40%", "tech_stack": ["github-actions", "ci", "python"], "severity": "info"},
]

# ── Metrics Tracking ─────────────────────────────────────────────────────

@dataclass
class AgentMetrics:
    role: str
    layer: int
    model_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    wall_time_sec: float = 0.0
    context_tokens_before_compression: int = 0
    context_tokens_after_compression: int = 0
    memory_lessons_retrieved: int = 0
    memory_lessons_relevant: int = 0  # judged by LLM
    output_text: str = ""

@dataclass
class PipelineMetrics:
    name: str
    agents: list[AgentMetrics] = field(default_factory=list)
    total_time_sec: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    estimated_cost: float = 0.0


# ── Pipeline Implementations ─────────────────────────────────────────────

def _call_llm(messages: list[dict], model: str = "gpt-4.1-mini") -> tuple[str, int, int]:
    """Call the LLM and return (response_text, input_tokens, output_tokens)."""
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=4000,
    )
    text = response.choices[0].message.content or ""
    usage = response.usage
    return text, usage.prompt_tokens, usage.completion_tokens


def _build_shared_context(completed_agents: list[AgentMetrics], pipeline: str) -> str:
    """Build the shared context from completed agents, applying compression."""
    if not completed_agents:
        return ""

    raw_entries = []
    for agent in completed_agents:
        entry = f"[{agent.role}] Layer {agent.layer} — Model: {agent.model_used}\n"
        entry += f"Output:\n{agent.output_text}\n"
        raw_entries.append(entry)

    raw_context = "\n---\n".join(raw_entries)

    if pipeline == "old":
        # Heuristic compression
        from context_compressor import _heuristic_compress
        compressed = _heuristic_compress(raw_context)
    else:
        # LLMLingua compression
        from context_compressor import compress_context_smart
        compressed = compress_context_smart(raw_context)

    return compressed, len(raw_context.split()), len(compressed.split())


def _get_memory_context(task_prompt: str, tech_stack: list[str], pipeline: str, mem_store) -> tuple[str, int]:
    """Retrieve relevant lessons from memory."""
    if pipeline == "old":
        # JSON keyword matching
        lessons = mem_store.get_lessons(tech_stack=tech_stack, limit=3)
    else:
        # ChromaDB semantic search
        lessons = mem_store.get_lessons(query=task_prompt, tech_stack=tech_stack, limit=3)

    if not lessons:
        return "", 0

    context = "<past_project_lessons>\n"
    for l in lessons:
        context += f"- [{l.get('severity', 'info').upper()}] {l['lesson']}\n"
    context += "</past_project_lessons>\n"

    return context, len(lessons)


def _route_model(role: str, prompt: str, pipeline: str) -> str:
    """Determine which model to use."""
    if pipeline == "old":
        return "gpt-4.1-mini"  # Static: always use strong
    else:
        # Smart routing
        from smart_router import _builtin_classify, SMART_ROUTER_THRESHOLD
        complexity = _builtin_classify(prompt, role)
        if role.lower() in {"architect", "tech_lead", "pm", "code_reviewer"}:
            return "gpt-4.1-mini"  # Strong
        if complexity >= SMART_ROUTER_THRESHOLD:
            return "gpt-4.1-mini"  # Strong
        return "gpt-4.1-nano"  # Weak (cheaper)


def run_pipeline(pipeline_name: str, mem_store) -> PipelineMetrics:
    """Run a full project build simulation through the DAG."""
    print(f"\n{'='*70}")
    print(f"RUNNING PIPELINE: {pipeline_name.upper()}")
    print(f"{'='*70}")

    metrics = PipelineMetrics(name=pipeline_name)
    completed_agents: list[AgentMetrics] = []
    pipeline_start = time.perf_counter()

    for layer_def in DAG_LAYERS:
        layer_num = layer_def["layer"]
        print(f"\n--- Layer {layer_num} ---")

        for agent_def in layer_def["agents"]:
            role = agent_def["role"]
            base_prompt = agent_def["prompt"]

            # 1. Route model
            model = _route_model(role, base_prompt, pipeline_name)

            # 2. Build shared context from previous layers
            if completed_agents:
                shared_ctx, raw_tokens, compressed_tokens = _build_shared_context(
                    completed_agents, pipeline_name
                )
            else:
                shared_ctx, raw_tokens, compressed_tokens = "", 0, 0

            # 3. Get memory context
            tech_stack = ["fastapi", "python", "sqlite", "sqlalchemy", "pytest", "docker"]
            memory_ctx, memory_count = _get_memory_context(
                base_prompt, tech_stack, pipeline_name, mem_store
            )

            # 4. Assemble final prompt
            system_msg = f"You are working on the TaskFlow project. Your role: {role}."
            if shared_ctx:
                system_msg += f"\n\nContext from previous agents:\n{shared_ctx}"
            if memory_ctx:
                system_msg += f"\n\nLessons from past projects:\n{memory_ctx}"

            messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": base_prompt},
            ]

            # 5. Call LLM
            print(f"  [{role}] model={model}, context={compressed_tokens} tokens, memory={memory_count} lessons...", end="", flush=True)
            t0 = time.perf_counter()
            try:
                output, in_tok, out_tok = _call_llm(messages, model=model)
            except Exception as e:
                print(f" ERROR: {e}")
                output, in_tok, out_tok = f"Error: {e}", 0, 0
            wall_time = time.perf_counter() - t0
            print(f" done ({wall_time:.1f}s, {in_tok}+{out_tok} tokens)")

            # 6. Record metrics
            agent_metrics = AgentMetrics(
                role=role,
                layer=layer_num,
                model_used=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                wall_time_sec=round(wall_time, 2),
                context_tokens_before_compression=raw_tokens,
                context_tokens_after_compression=compressed_tokens,
                memory_lessons_retrieved=memory_count,
                output_text=output[:3000],  # Truncate for storage
            )
            metrics.agents.append(agent_metrics)
            completed_agents.append(agent_metrics)

    metrics.total_time_sec = round(time.perf_counter() - pipeline_start, 2)
    metrics.total_input_tokens = sum(a.input_tokens for a in metrics.agents)
    metrics.total_output_tokens = sum(a.output_tokens for a in metrics.agents)

    # Estimate cost (gpt-4.1-mini: $0.40/1M input, $1.60/1M output; nano: $0.10/1M, $0.40/1M)
    for a in metrics.agents:
        if a.model_used == "gpt-4.1-nano":
            metrics.estimated_cost += (a.input_tokens * 0.10 + a.output_tokens * 0.40) / 1_000_000
        else:
            metrics.estimated_cost += (a.input_tokens * 0.40 + a.output_tokens * 1.60) / 1_000_000
    metrics.estimated_cost = round(metrics.estimated_cost, 4)

    return metrics


# ── LLM-as-Judge Review ─────────────────────────────────────────────────

def judge_pipeline_output(metrics: PipelineMetrics) -> dict:
    """Have an LLM judge the quality of the pipeline's output."""
    print(f"\n[*] Judging {metrics.name} pipeline output...")

    # Collect all code outputs
    all_outputs = ""
    for a in metrics.agents:
        all_outputs += f"\n\n=== {a.role} (Layer {a.layer}) ===\n{a.output_text[:2000]}"

    prompt = f"""You are a senior engineering manager reviewing the output of an AI agent team that built a task management API called "TaskFlow".

Review the following outputs from each agent and score each dimension from 1-10:

{all_outputs[:12000]}

Score these dimensions:
1. **Completeness** (1-10): Did the team produce all required components? (API, models, tests, Docker, CI)
2. **Code Quality** (1-10): Is the code clean, well-structured, follows best practices?
3. **Correctness** (1-10): Would the code actually work if run? Are there bugs?
4. **Security** (1-10): Are there security considerations? Input validation? SQL injection prevention?
5. **Testing** (1-10): Are tests comprehensive? Do they cover edge cases?
6. **Documentation** (1-10): Are there comments, docstrings, README content?
7. **Architecture** (1-10): Is the system well-designed? Separation of concerns?
8. **Memory Utilization** (1-10): Did agents appear to leverage past project lessons effectively?

Reply with ONLY a JSON object:
{{
  "completeness": <score>,
  "code_quality": <score>,
  "correctness": <score>,
  "security": <score>,
  "testing": <score>,
  "documentation": <score>,
  "architecture": <score>,
  "memory_utilization": <score>,
  "overall": <score>,
  "strengths": ["list of strengths"],
  "weaknesses": ["list of weaknesses"],
  "critical_issues": ["list of critical bugs or issues found"]
}}"""

    try:
        response, _, _ = _call_llm(
            [{"role": "user", "content": prompt}],
            model="gpt-4.1-mini",
        )
        # Parse JSON from response
        content = response.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content)
    except Exception as e:
        print(f"  [!] Judging failed: {e}")
        return {"error": str(e)}


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("HiveMind E2E Project Simulation Benchmark")
    print("=" * 70)
    print(f"Project: TaskFlow (Task Management API)")
    print(f"DAG: 5 layers, 7 agents")
    print(f"LLM: gpt-4.1-mini (strong) / gpt-4.1-nano (weak)")
    print()

    tmp_dir = tempfile.mkdtemp(prefix="hivemind_e2e_")

    try:
        # Initialize memory stores
        import importlib
        import cross_project_memory as cpm

        # OLD pipeline: JSON memory
        os.environ["MEMORY_BACKEND"] = "json"
        importlib.reload(cpm)
        json_mem = cpm.CrossProjectMemory(os.path.join(tmp_dir, "json_store"))
        for lesson in PAST_LESSONS:
            json_mem.add_lesson(**lesson)

        # NEW pipeline: ChromaDB memory
        os.environ["MEMORY_BACKEND"] = "chroma"
        importlib.reload(cpm)
        chroma_mem = cpm.CrossProjectMemory(os.path.join(tmp_dir, "chroma_store"))
        for lesson in PAST_LESSONS:
            chroma_mem.add_lesson(**lesson)

        # Run OLD pipeline
        old_metrics = run_pipeline("old", json_mem)

        # Run NEW pipeline
        new_metrics = run_pipeline("new", chroma_mem)

        # Judge both outputs
        old_scores = judge_pipeline_output(old_metrics)
        new_scores = judge_pipeline_output(new_metrics)

        # ── Print Comparison ─────────────────────────────────────────
        print("\n" + "=" * 70)
        print("E2E BENCHMARK RESULTS")
        print("=" * 70)

        # Per-agent comparison
        print(f"\n{'Agent':<18} {'Pipeline':<8} {'Model':<18} {'Time':>6} {'In Tok':>8} {'Out Tok':>8} {'Ctx Comp':>10} {'Memory':>7}")
        print("-" * 100)
        for old_a, new_a in zip(old_metrics.agents, new_metrics.agents):
            ctx_old = f"{old_a.context_tokens_after_compression}/{old_a.context_tokens_before_compression}" if old_a.context_tokens_before_compression else "N/A"
            ctx_new = f"{new_a.context_tokens_after_compression}/{new_a.context_tokens_before_compression}" if new_a.context_tokens_before_compression else "N/A"
            print(f"{old_a.role:<18} {'OLD':<8} {old_a.model_used:<18} {old_a.wall_time_sec:>5.1f}s {old_a.input_tokens:>8} {old_a.output_tokens:>8} {ctx_old:>10} {old_a.memory_lessons_retrieved:>7}")
            print(f"{'':<18} {'NEW':<8} {new_a.model_used:<18} {new_a.wall_time_sec:>5.1f}s {new_a.input_tokens:>8} {new_a.output_tokens:>8} {ctx_new:>10} {new_a.memory_lessons_retrieved:>7}")
            print()

        # Summary
        print(f"\n{'='*70}")
        print(f"{'Metric':<35} {'OLD Pipeline':>18} {'NEW Pipeline':>18} {'Delta':>12}")
        print(f"{'-'*70}")
        print(f"{'Total Time':<35} {old_metrics.total_time_sec:>17.1f}s {new_metrics.total_time_sec:>17.1f}s {new_metrics.total_time_sec - old_metrics.total_time_sec:>+11.1f}s")
        print(f"{'Total Input Tokens':<35} {old_metrics.total_input_tokens:>18,} {new_metrics.total_input_tokens:>18,} {new_metrics.total_input_tokens - old_metrics.total_input_tokens:>+12,}")
        print(f"{'Total Output Tokens':<35} {old_metrics.total_output_tokens:>18,} {new_metrics.total_output_tokens:>18,} {new_metrics.total_output_tokens - old_metrics.total_output_tokens:>+12,}")
        print(f"{'Estimated Cost':<35} ${old_metrics.estimated_cost:>17.4f} ${new_metrics.estimated_cost:>17.4f} ${new_metrics.estimated_cost - old_metrics.estimated_cost:>+11.4f}")

        # Quality scores
        if "error" not in old_scores and "error" not in new_scores:
            print(f"\n{'Quality Dimension':<35} {'OLD Score':>18} {'NEW Score':>18} {'Delta':>12}")
            print(f"{'-'*70}")
            dimensions = ["completeness", "code_quality", "correctness", "security",
                          "testing", "documentation", "architecture", "memory_utilization", "overall"]
            for dim in dimensions:
                old_s = old_scores.get(dim, "N/A")
                new_s = new_scores.get(dim, "N/A")
                if isinstance(old_s, (int, float)) and isinstance(new_s, (int, float)):
                    delta = new_s - old_s
                    marker = " ★" if delta > 0 else (" ▼" if delta < 0 else "")
                    print(f"{dim:<35} {old_s:>17}/10 {new_s:>17}/10 {delta:>+11}{marker}")
                else:
                    print(f"{dim:<35} {old_s:>18} {new_s:>18}")

            # Strengths/weaknesses
            print(f"\n--- OLD Pipeline Strengths ---")
            for s in old_scores.get("strengths", []):
                print(f"  + {s}")
            print(f"\n--- OLD Pipeline Weaknesses ---")
            for w in old_scores.get("weaknesses", []):
                print(f"  - {w}")
            print(f"\n--- NEW Pipeline Strengths ---")
            for s in new_scores.get("strengths", []):
                print(f"  + {s}")
            print(f"\n--- NEW Pipeline Weaknesses ---")
            for w in new_scores.get("weaknesses", []):
                print(f"  - {w}")

        # Save results
        results = {
            "old_pipeline": {
                "metrics": asdict(old_metrics),
                "quality_scores": old_scores,
            },
            "new_pipeline": {
                "metrics": asdict(new_metrics),
                "quality_scores": new_scores,
            },
        }
        # Remove large output_text from saved metrics
        for pipeline in ["old_pipeline", "new_pipeline"]:
            for agent in results[pipeline]["metrics"]["agents"]:
                agent["output_text"] = agent["output_text"][:500] + "..."

        results_file = Path(__file__).parent / "e2e_results.json"
        with open(results_file, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\n[*] Full results saved to {results_file}")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n{'='*70}")
    print("E2E BENCHMARK COMPLETE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
