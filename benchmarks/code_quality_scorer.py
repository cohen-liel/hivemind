#!/usr/bin/env python3
"""Automated Code Quality Scorer using gpt-4.1-mini.

Reads all .py files from a project directory and scores them on multiple
dimensions. Returns a structured quality report with numeric scores (1-10)
and qualitative feedback.

Scoring dimensions:
1. Code Structure & Organization (separation of concerns, modularity)
2. Error Handling & Robustness (try/except, input validation, edge cases)
3. Code Readability (naming, comments, docstrings, formatting)
4. Best Practices (type hints, DRY, SOLID principles)
5. Security (SQL injection, input sanitization, secrets handling)
6. Test Quality (coverage breadth, edge cases, assertion quality) — only for test files
"""

import json
import os
import sys

from openai import OpenAI


def collect_project_files(project_dir: str) -> dict[str, str]:
    """Collect all .py files from the project directory."""
    files = {}
    for f in sorted(os.listdir(project_dir)):
        if f.endswith(".py") and not f.startswith("."):
            fpath = os.path.join(project_dir, f)
            with open(fpath, encoding="utf-8", errors="replace") as fh:
                files[f] = fh.read()
    return files


REVIEW_PROMPT = """\
You are a senior code reviewer. Analyze the following Python project files and score them.

PROJECT FILES:
{file_contents}

Score each dimension from 1-10 (10 = excellent). Be honest and critical.
Also provide a brief explanation for each score.

Return ONLY valid JSON in this exact format:
{{
  "scores": {{
    "structure_organization": {{"score": <1-10>, "explanation": "<why>"}},
    "error_handling": {{"score": <1-10>, "explanation": "<why>"}},
    "readability": {{"score": <1-10>, "explanation": "<why>"}},
    "best_practices": {{"score": <1-10>, "explanation": "<why>"}},
    "security": {{"score": <1-10>, "explanation": "<why>"}},
    "test_quality": {{"score": <1-10>, "explanation": "<why>"}}
  }},
  "overall_score": <1-10 weighted average>,
  "strengths": ["<strength1>", "<strength2>"],
  "weaknesses": ["<weakness1>", "<weakness2>"],
  "critical_issues": ["<issue1 if any>"]
}}
"""


def score_project(project_dir: str) -> dict:
    """Score a project's code quality using gpt-4.1-mini."""
    client = OpenAI()

    files = collect_project_files(project_dir)
    if not files:
        return {"error": "No Python files found", "overall_score": 0}

    # Build file contents string
    file_contents = ""
    for fname, content in files.items():
        file_contents += f"\n--- {fname} ---\n{content}\n"

    # Truncate if too long (keep under ~60K chars for context window)
    if len(file_contents) > 60000:
        file_contents = file_contents[:60000] + "\n\n... [truncated]"

    prompt = REVIEW_PROMPT.format(file_contents=file_contents)

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a strict but fair senior code reviewer. Return only valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=2000,
            temperature=0.1,
        )

        text = response.choices[0].message.content.strip()
        # Extract JSON from possible markdown fences
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        result = json.loads(text)
        result["files_reviewed"] = list(files.keys())
        result["total_lines"] = sum(c.count("\n") + 1 for c in files.values())
        return result

    except json.JSONDecodeError as e:
        return {
            "error": f"Failed to parse review JSON: {e}",
            "raw_response": text,
            "overall_score": 0,
        }
    except Exception as e:
        return {"error": f"API error: {e}", "overall_score": 0}


def print_quality_report(result: dict, label: str = ""):
    """Print a formatted quality report."""
    if "error" in result:
        print(f"\n{'=' * 60}")
        print(f"CODE QUALITY REPORT {label}")
        print(f"Error: {result['error']}")
        print(f"{'=' * 60}")
        return

    print(f"\n{'=' * 60}")
    print(f"CODE QUALITY REPORT {label}")
    print(f"{'=' * 60}")
    print(f"Files reviewed: {', '.join(result.get('files_reviewed', []))}")
    print(f"Total lines: {result.get('total_lines', 'N/A')}")
    print(f"\nOverall Score: {result.get('overall_score', 'N/A')}/10")
    print(f"\n{'Dimension':<30} {'Score':<8} {'Explanation'}")
    print("-" * 80)

    for dim, data in result.get("scores", {}).items():
        name = dim.replace("_", " ").title()
        score = data.get("score", "?")
        explanation = data.get("explanation", "")[:60]
        print(f"{name:<30} {score:<8} {explanation}")

    if result.get("strengths"):
        print("\nStrengths:")
        for s in result["strengths"]:
            print(f"  + {s}")

    if result.get("weaknesses"):
        print("\nWeaknesses:")
        for w in result["weaknesses"]:
            print(f"  - {w}")

    if result.get("critical_issues"):
        print("\nCritical Issues:")
        for i in result["critical_issues"]:
            print(f"  !! {i}")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 code_quality_scorer.py <project_dir> [label]")
        sys.exit(1)

    project_dir = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else ""

    result = score_project(project_dir)
    print_quality_report(result, label)

    # Also save as JSON
    output_file = os.path.join(project_dir, "code_quality_report.json")
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved to: {output_file}")
