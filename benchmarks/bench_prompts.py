"""Prompt Enhancement Benchmark — measures the impact of structured output
schemas, few-shot examples, and downstream contracts on agent output quality.

Runs the same 3 agents (database_expert, backend_developer, reviewer) with:
1. BASELINE: plain role prompt only
2. ENHANCED: role prompt + output schema + few-shot + downstream contract + self-check

Evaluates:
- Output structure compliance (does output follow the schema?)
- Completeness (are all required sections present?)
- Actionability (are review findings specific with file:line references?)
- Downstream readiness (does output contain what the next agent needs?)
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from prompt_enhancer import build_enhancement, inject_enhancements, OUTPUT_SCHEMAS

client = OpenAI()
MODEL = "gpt-4.1-mini"

# ── Test Scenarios ───────────────────────────────────────────────────────

SCENARIOS = [
    {
        "role": "backend_developer",
        "goal": "Implement FastAPI CRUD endpoints for a task management API with SQLite database",
        "acceptance_criteria": [
            "POST /tasks endpoint",
            "GET /tasks with filtering",
            "GET /tasks/{id}",
            "PUT /tasks/{id}",
            "DELETE /tasks/{id}",
            "Pydantic models for all request/response",
        ],
        "downstream_roles": ["test_engineer", "reviewer", "security_auditor"],
    },
    {
        "role": "reviewer",
        "goal": "Review the task management API code for quality, security, and best practices",
        "acceptance_criteria": [
            "Check for SQL injection",
            "Verify input validation",
            "Check error handling",
            "Assess test coverage",
        ],
        "downstream_roles": [],
        "context": """Here is the code to review:
```python
from fastapi import FastAPI, HTTPException
import sqlite3

app = FastAPI()

def get_db():
    conn = sqlite3.connect("tasks.db")
    return conn

@app.post("/tasks")
def create_task(title: str, description: str = ""):
    db = get_db()
    db.execute(f"INSERT INTO tasks (title, description) VALUES ('{title}', '{description}')")
    db.commit()
    return {"status": "created"}

@app.get("/tasks/{task_id}")
def get_task(task_id: int):
    db = get_db()
    result = db.execute(f"SELECT * FROM tasks WHERE id = {task_id}").fetchone()
    if not result:
        raise HTTPException(status_code=404)
    return {"task": result}

@app.delete("/tasks/{task_id}")
def delete_task(task_id):
    db = get_db()
    db.execute(f"DELETE FROM tasks WHERE id = {task_id}")
    db.commit()
    return {"status": "deleted"}
```""",
    },
    {
        "role": "database_expert",
        "goal": "Design and implement SQLite database schema for task management system",
        "acceptance_criteria": [
            "Tasks table with id, title, description, status, priority, created_at, updated_at",
            "Proper indexes on status and priority",
            "Database initialization function",
        ],
        "downstream_roles": ["backend_developer"],
    },
]


def run_agent(scenario: dict, use_enhancement: bool) -> dict:
    """Run a single agent with or without prompt enhancement."""
    role = scenario["role"]
    goal = scenario["goal"]
    criteria = scenario.get("acceptance_criteria", [])
    context = scenario.get("context", "")

    # Build base prompt
    base_prompt = f"You are a {role}. Complete this task:\n\n**Goal:** {goal}\n"
    if criteria:
        base_prompt += "\n**Acceptance Criteria:**\n" + "\n".join(f"- {c}" for c in criteria) + "\n"
    if context:
        base_prompt += f"\n{context}\n"

    # Apply enhancement
    if use_enhancement:
        enhancement = build_enhancement(
            role=role,
            downstream_roles=scenario.get("downstream_roles", []),
            acceptance_criteria=criteria,
        )
        base_prompt = inject_enhancements(base_prompt, enhancement)

    t0 = time.time()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": base_prompt}],
        max_tokens=2500,
        temperature=0.3,
    )
    elapsed = time.time() - t0
    output = response.choices[0].message.content or ""

    return {
        "role": role,
        "output": output,
        "tokens_in": response.usage.prompt_tokens,
        "tokens_out": response.usage.completion_tokens,
        "elapsed": elapsed,
        "enhanced": use_enhancement,
    }


def evaluate_output(role: str, output: str, criteria: list[str]) -> dict:
    """Use LLM-as-judge to evaluate output quality."""
    eval_prompt = f"""Evaluate this {role} agent output on a scale of 1-10 for each dimension.

**Output to evaluate:**
{output[:4000]}

**Acceptance Criteria:**
{chr(10).join(f'- {c}' for c in criteria)}

Rate STRICTLY:
1. structure_compliance: Does the output follow a clear, structured format with labeled sections? (1-10)
2. completeness: Are ALL acceptance criteria addressed? (1-10)
3. actionability: Are findings specific (file:line references, exact commands)? (1-10)
4. downstream_readiness: Could the next agent (tester/reviewer) use this output effectively? (1-10)
5. professional_quality: Is this production-grade output? (1-10)

Respond with JSON only:
{{"structure_compliance": N, "completeness": N, "actionability": N, "downstream_readiness": N, "professional_quality": N, "key_observation": "one sentence"}}"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": eval_prompt}],
            max_tokens=300,
            temperature=0.1,
        )
        text = response.choices[0].message.content or ""
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception as e:
        return {"error": str(e)}
    return {}


def check_section_presence(role: str, output: str) -> dict:
    """Check if expected sections are present in the output."""
    role_key = role.lower().replace(" ", "_")
    schema = OUTPUT_SCHEMAS.get(role_key, "")

    # Extract expected section headers from schema
    expected_sections = []
    for line in schema.split("\n"):
        line = line.strip()
        if line.startswith("## "):
            expected_sections.append(line.replace("## ", "").strip())

    found = {}
    for section in expected_sections:
        # Check if section header appears in output (case-insensitive)
        found[section] = section.lower() in output.lower() or \
                         section.upper() in output.upper()

    return found


def main():
    print("=" * 70)
    print("PROMPT ENHANCEMENT BENCHMARK")
    print("=" * 70)

    all_results = []

    for scenario in SCENARIOS:
        role = scenario["role"]
        criteria = scenario.get("acceptance_criteria", [])

        print(f"\n{'─'*60}")
        print(f"Agent: {role}")
        print(f"{'─'*60}")

        # Run baseline
        print(f"  Running baseline...", end=" ", flush=True)
        baseline = run_agent(scenario, use_enhancement=False)
        print(f"done ({baseline['elapsed']:.1f}s)")

        # Run enhanced
        print(f"  Running enhanced...", end=" ", flush=True)
        enhanced = run_agent(scenario, use_enhancement=True)
        print(f"done ({enhanced['elapsed']:.1f}s)")

        # Evaluate both
        print(f"  Evaluating baseline...", end=" ", flush=True)
        baseline_scores = evaluate_output(role, baseline["output"], criteria)
        print("done")

        print(f"  Evaluating enhanced...", end=" ", flush=True)
        enhanced_scores = evaluate_output(role, enhanced["output"], criteria)
        print("done")

        # Check section presence
        baseline_sections = check_section_presence(role, baseline["output"])
        enhanced_sections = check_section_presence(role, enhanced["output"])

        result = {
            "role": role,
            "baseline_scores": baseline_scores,
            "enhanced_scores": enhanced_scores,
            "baseline_sections": baseline_sections,
            "enhanced_sections": enhanced_sections,
            "baseline_tokens": baseline["tokens_in"] + baseline["tokens_out"],
            "enhanced_tokens": enhanced["tokens_in"] + enhanced["tokens_out"],
        }
        all_results.append(result)

        # Print comparison
        print(f"\n  {'Dimension':<25} {'Baseline':>10} {'Enhanced':>10} {'Delta':>10}")
        print(f"  {'-'*55}")
        for dim in ["structure_compliance", "completeness", "actionability",
                     "downstream_readiness", "professional_quality"]:
            b = baseline_scores.get(dim, "?")
            e = enhanced_scores.get(dim, "?")
            delta = ""
            if isinstance(b, (int, float)) and isinstance(e, (int, float)):
                d = e - b
                delta = f"+{d}" if d > 0 else str(d)
            print(f"  {dim:<25} {str(b):>10} {str(e):>10} {delta:>10}")

        # Section presence
        print(f"\n  Section Presence:")
        for section in enhanced_sections:
            b = "✓" if baseline_sections.get(section, False) else "✗"
            e = "✓" if enhanced_sections.get(section, False) else "✗"
            print(f"    {section:<30} Baseline: {b}  Enhanced: {e}")

        print(f"\n  Tokens: Baseline={baseline['tokens_in']+baseline['tokens_out']}, Enhanced={enhanced['tokens_in']+enhanced['tokens_out']}")
        print(f"  Baseline observation: {baseline_scores.get('key_observation', 'N/A')}")
        print(f"  Enhanced observation: {enhanced_scores.get('key_observation', 'N/A')}")

    # Summary
    print(f"\n{'='*70}")
    print("AGGREGATE SUMMARY")
    print(f"{'='*70}")

    dims = ["structure_compliance", "completeness", "actionability",
            "downstream_readiness", "professional_quality"]

    print(f"\n  {'Dimension':<25} {'Avg Baseline':>12} {'Avg Enhanced':>12} {'Avg Delta':>10}")
    print(f"  {'-'*60}")

    for dim in dims:
        b_scores = [r["baseline_scores"].get(dim, 0) for r in all_results
                     if isinstance(r["baseline_scores"].get(dim), (int, float))]
        e_scores = [r["enhanced_scores"].get(dim, 0) for r in all_results
                     if isinstance(r["enhanced_scores"].get(dim), (int, float))]
        b_avg = sum(b_scores) / len(b_scores) if b_scores else 0
        e_avg = sum(e_scores) / len(e_scores) if e_scores else 0
        delta = e_avg - b_avg
        delta_str = f"+{delta:.1f}" if delta > 0 else f"{delta:.1f}"
        print(f"  {dim:<25} {b_avg:>12.1f} {e_avg:>12.1f} {delta_str:>10}")

    # Overall
    all_b = [r["baseline_scores"].get(d, 0) for r in all_results for d in dims
             if isinstance(r["baseline_scores"].get(d), (int, float))]
    all_e = [r["enhanced_scores"].get(d, 0) for r in all_results for d in dims
             if isinstance(r["enhanced_scores"].get(d), (int, float))]
    b_overall = sum(all_b) / len(all_b) if all_b else 0
    e_overall = sum(all_e) / len(all_e) if all_e else 0
    print(f"\n  {'OVERALL AVERAGE':<25} {b_overall:>12.1f} {e_overall:>12.1f} {f'+{e_overall-b_overall:.1f}':>10}")

    # Save results
    output_path = Path(__file__).parent / "prompt_benchmark_results.json"
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
