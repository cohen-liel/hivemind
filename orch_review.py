"""Review & evaluation logic for the orchestrator.

Extracted from orchestrator.py to reduce file size.
All functions operate on an OrchestratorManager instance passed as `mgr`.
This module handles:
  - Building structured review prompts from sub-agent results
  - Auto-evaluation (run tests, auto-retry developer on failures)
  - Delegation parsing
  - File change detection
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from config import (
    MAX_ORCHESTRATOR_LOOPS,
    PYTEST_TIMEOUT,
    SUBPROCESS_LONG_TIMEOUT,
    SUBPROCESS_SHORT_TIMEOUT,
)

if TYPE_CHECKING:
    from sdk_client import SDKResponse

logger = logging.getLogger(__name__)

# Regex to parse <delegate> blocks
_DELEGATE_RE = re.compile(r"<delegate>(.*?)</delegate>", re.DOTALL)


# ── Delegation parsing ────────────────────────────────────────────────

from _shared_utils import extract_json as _extract_json


def parse_delegations(text: str):
    """Parse <delegate> blocks from orchestrator output.

    Returns a list of Delegation namedtuples (imported from orchestrator).
    """
    from orchestrator import Delegation

    delegations = []
    for match in _DELEGATE_RE.finditer(text):
        raw = match.group(1)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = _extract_json(raw)
        if data and isinstance(data, dict):
            agent = data.get("agent", "developer")
            task = data.get("task", "")
            if task:
                skills_list = data.get("skills", [])
                if isinstance(skills_list, str):
                    skills_list = [skills_list]
                delegations.append(
                    Delegation(
                        agent=agent,
                        task=task,
                        context=data.get("context", ""),
                        skills=skills_list,
                    )
                )
                logger.info(f"Parsed delegation: {agent} -> {task[:80]}")
            else:
                logger.warning(f"Delegation block missing 'task': {raw[:200]}")
        else:
            logger.warning(f"Failed to parse delegation JSON: {raw[:200]}")

    if not delegations and "<delegate>" in text:
        logger.error(
            f"Found <delegate> tags but failed to parse any delegations! "
            f"Raw text around tags: {text[text.find('<delegate>') : text.find('</delegate>') + 20][:500]}"
        )
    return delegations


def strip_delegate_blocks(text: str) -> str:
    """Remove <delegate>...</delegate> blocks from text for display purposes."""
    return re.sub(r"<delegate>.*?</delegate>", "", text, flags=re.DOTALL).strip()


def extract_section(text: str, markers: list[str], max_lines: int = 10) -> str:
    """Extract a section from agent output text by looking for markdown headers."""
    for marker in markers:
        idx = text.find(marker)
        if idx >= 0:
            end = text.find("\n## ", idx + len(marker))
            section = text[idx + len(marker) : end if end > idx else idx + 800].strip()
            lines = section.split("\n")[:max_lines]
            return "\n".join(lines).strip()
    return ""


# ── File change detection ─────────────────────────────────────────────


async def detect_file_changes(mgr) -> str:
    """Run git status in the project dir to show what files the agent changed."""
    try:

        async def _git(*args: str) -> str:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=mgr.project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=SUBPROCESS_SHORT_TIMEOUT)
            return stdout.decode("utf-8", errors="replace")

        diff_out = await _git("diff", "--stat", "HEAD")
        if diff_out.strip():
            return diff_out.strip()
        status_out = await _git("status", "--short")
        return status_out.strip() or "(no file changes detected)"
    except Exception:
        return "(unable to detect changes)"


# ── Review prompt builder ─────────────────────────────────────────────


async def build_review_prompt(
    mgr, sub_results: dict[str, list[SDKResponse]], completed_rounds: list[str] | None = None
) -> str:
    """Build a structured prompt for the orchestrator to review sub-agent results.

    This is the CRITICAL method that drives the orchestration loop forward.
    """
    parts: list[str] = []

    # Budget / rounds context
    budget_used = mgr.total_cost_usd
    budget_cap = mgr._effective_budget
    budget_left = max(0.0, budget_cap - budget_used)
    loops_done = mgr._current_loop
    loops_max = MAX_ORCHESTRATOR_LOOPS
    loops_left = max(0, loops_max - loops_done)
    burn_rate = budget_used / max(loops_done, 1)
    budget_rounds_left = int(budget_left / burn_rate) if burn_rate > 0 else loops_left
    effective_rounds_left = min(loops_left, budget_rounds_left)
    parts.append(
        f"<round_status>\n"
        f"  <progress>Round {loops_done}/{loops_max} | Budget ${budget_used:.2f}/${budget_cap:.0f} (~{effective_rounds_left} rounds left)</progress>"
    )
    if effective_rounds_left < 5:
        parts.append("  <warning>BUDGET LOW — prioritize critical work only!</warning>")
    parts.append("</round_status>")

    # Parse each agent's output
    has_errors = False
    successful_agents: list[str] = []
    failed_agents: list[str] = []
    crashed_agents: list[str] = []
    _findings: list[dict] = []
    _agent_summaries: dict[str, str] = {}
    _agents_only_reports: list[str] = []
    _agents_wrote_code: list[str] = []
    _test_results: list[dict] = []

    _CRASH_INDICATORS = (
        "session crashed",
        "session was interrupted",
        "pick up where I left off",
        "was in the middle of",
        "got cancelled",
        "timed out",
        "anyio bug",
        "cancel scope",
        "RuntimeError",
    )

    for agent, responses in sub_results.items():
        for response in responses:
            is_soft_crash = False
            if not response.is_error and response.text:
                text_lower = response.text[:1000].lower()
                if any(ind in text_lower for ind in _CRASH_INDICATORS):
                    is_soft_crash = True

            if response.is_error or is_soft_crash:
                has_errors = True
                if is_soft_crash:
                    crashed_agents.append(agent)
                else:
                    failed_agents.append(agent)
            else:
                successful_agents.append(agent)

            text = response.text or ""

            summary = extract_section(
                text, ["## SUMMARY", "## Summary", "### Summary", "## Result"]
            )
            status_line = extract_section(text, ["## STATUS", "## Status"], max_lines=2)
            issues_text = extract_section(
                text, ["## ISSUES FOUND", "## Issues Found", "## Issues", "## Findings"]
            )
            files_section = extract_section(text, ["## FILES CHANGED", "## Files Changed"])

            if response.is_error:
                agent_summary = f"FAILED: {response.error_message[:300]}"
            elif is_soft_crash:
                agent_summary = f"CRASHED: {text[:300]}"
            elif summary:
                agent_summary = summary[:600]
            else:
                agent_summary = text[:600]
            _agent_summaries[agent] = agent_summary

            # Extract specific findings
            if issues_text:
                for line in issues_text.split("\n"):
                    line = line.strip()
                    if not line or line.startswith("#") or line in ("(or: none)", "none", "None"):
                        continue
                    severity = "MEDIUM"
                    for sev in ["CRITICAL", "HIGH", "LOW"]:
                        if sev in line.upper():
                            severity = sev
                            break
                    file_path = ""
                    for token in line.split():
                        cleaned = token.strip("`\"',;:()[]")
                        if ("/" in cleaned or "." in cleaned) and len(cleaned) > 3:
                            if not cleaned.startswith("http") and any(
                                cleaned.endswith(ext)
                                for ext in (
                                    ".py",
                                    ".ts",
                                    ".tsx",
                                    ".js",
                                    ".jsx",
                                    ".css",
                                    ".html",
                                    ".yml",
                                    ".yaml",
                                    ".toml",
                                    ".md",
                                )
                            ):
                                file_path = cleaned
                                break
                    _findings.append(
                        {
                            "agent": agent,
                            "type": "issue",
                            "description": line[:200],
                            "file": file_path,
                            "severity": severity,
                        }
                    )

            # Extract test results
            text_upper = text.upper()
            if agent in ("tester",) or "TEST" in text_upper:
                passed = failed = errors = 0
                for line in text.split("\n"):
                    ll = line.lower().strip()
                    tokens = ll.split()
                    for ti, tok in enumerate(tokens):
                        if tok.isdigit():
                            num = int(tok)
                            next_tok = tokens[ti + 1] if ti + 1 < len(tokens) else ""
                            if "passed" in next_tok or next_tok.startswith("pass"):
                                passed = max(passed, num)
                            elif (
                                "failed" in next_tok
                                or "failure" in next_tok
                                or next_tok.startswith("fail")
                            ):
                                failed = max(failed, num)
                            elif "error" in next_tok:
                                errors = max(errors, num)
                        elif (
                            tok in ("passed:", "passed")
                            and ti + 1 < len(tokens)
                            and tokens[ti + 1].isdigit()
                        ):
                            passed = max(passed, int(tokens[ti + 1]))
                        elif (
                            tok in ("failed:", "failed", "failures:")
                            and ti + 1 < len(tokens)
                            and tokens[ti + 1].isdigit()
                        ):
                            failed = max(failed, int(tokens[ti + 1]))
                        elif (
                            tok in ("errors:", "error:")
                            and ti + 1 < len(tokens)
                            and tokens[ti + 1].isdigit()
                        ):
                            errors = max(errors, int(tokens[ti + 1]))
                    if any(kw in ll for kw in ("fail:", "failed:", "error:", "assertion")):
                        _findings.append(
                            {
                                "agent": agent,
                                "type": "test_failure",
                                "description": line.strip()[:200],
                                "file": "",
                                "severity": "HIGH",
                            }
                        )
                if passed or failed or errors:
                    _test_results.append(
                        {"agent": agent, "passed": passed, "failed": failed, "errors": errors}
                    )

            # Detect report-only vs code changes
            _code_extensions = (
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
                ".css",
                ".html",
                ".yml",
                ".yaml",
                ".toml",
            )
            _report_extensions = (".md", ".txt", ".log", ".json")
            if files_section:
                has_code = any(ext in files_section for ext in _code_extensions)
                has_only_reports = (
                    all(
                        any(ext in line for ext in _report_extensions)
                        for line in files_section.split("\n")[1:]
                        if line.strip().startswith("-") or line.strip().startswith("*")
                    )
                    if files_section.strip()
                    else False
                )
                if has_only_reports and not has_code:
                    _agents_only_reports.append(agent)
                elif has_code:
                    _agents_wrote_code.append(agent)

            # Detect NEEDS_FOLLOWUP / BLOCKED
            if status_line:
                if "NEEDS_FOLLOWUP" in status_line:
                    _findings.append(
                        {
                            "agent": agent,
                            "type": "followup",
                            "description": status_line[:200],
                            "file": "",
                            "severity": "HIGH",
                        }
                    )
                elif "BLOCKED" in status_line:
                    _findings.append(
                        {
                            "agent": agent,
                            "type": "blocked",
                            "description": status_line[:200],
                            "file": "",
                            "severity": "CRITICAL",
                        }
                    )

    # Workspace changes (git)
    file_changes = await detect_file_changes(mgr)
    has_file_changes = file_changes and "(no file" not in file_changes

    # Build the prompt
    parts.append("\n<agent_results>")
    for agent, summary in _agent_summaries.items():
        status_tag = (
            "success"
            if agent in successful_agents
            else "failed"
            if agent in failed_agents
            else "crashed"
        )
        parts.append(f"  <agent name='{agent}' status='{status_tag}'>{summary[:500]}</agent>")
    parts.append("</agent_results>")

    if has_file_changes:
        parts.append(f"<files_changed>\n{file_changes}\n</files_changed>")

    critical_findings = [f for f in _findings if f["severity"] in ("CRITICAL", "HIGH")]
    medium_findings = [f for f in _findings if f["severity"] == "MEDIUM"]
    if critical_findings or medium_findings:
        parts.append("<issues_requiring_action>")
        for f in critical_findings:
            file_hint = f" file='{f['file']}'" if f["file"] else ""
            parts.append(
                f"  <issue severity='{f['severity']}' agent='{f['agent']}'{file_hint}>{f['description']}</issue>"
            )
        for f in medium_findings[:5]:
            file_hint = f" file='{f['file']}'" if f["file"] else ""
            parts.append(
                f"  <issue severity='{f['severity']}' agent='{f['agent']}'{file_hint}>{f['description']}</issue>"
            )
        parts.append("</issues_requiring_action>")

    if _test_results:
        parts.append("<test_results>")
        for tr in _test_results:
            parts.append(
                f"  <test agent='{tr['agent']}' passed='{tr['passed']}' failed='{tr['failed']}' errors='{tr['errors']}'/>"
            )
        parts.append("</test_results>")

    if completed_rounds:
        parts.append(f"<round_history count='{len(completed_rounds)}'>")
        for r in completed_rounds[-5:]:
            parts.append(f"  <round>{r}</round>")
        parts.append("</round_history>")

    # Build concise hints instead of ready-made <delegate> blocks.
    # The orchestrator should decide HOW to act, not just rubber-stamp
    # pre-built delegations. Hints give it the "what" without the "how".
    hints: list[str] = []

    # Crashed/failed agents
    _retried = set()
    for agent in crashed_agents + failed_agents:
        if agent in _retried:
            continue
        _retried.add(agent)
        error_ctx = _agent_summaries.get(agent, "unknown error")[:150]
        if error_ctx.startswith(("FAILED: ", "CRASHED: ")):
            error_ctx = error_ctx.split(": ", 1)[1]
        hints.append(f"{agent} FAILED: {error_ctx}")

    # Critical issues from reviewer/tester
    if critical_findings and not failed_agents:
        for f in critical_findings[:5]:
            file_hint = f" in {f['file']}" if f["file"] else ""
            hints.append(f"[{f['severity']}] {f['agent']}{file_hint}: {f['description'][:120]}")

    # Test failures
    test_failures = [f for f in _findings if f["type"] == "test_failure"]
    if test_failures and not failed_agents and not critical_findings:
        failure_count = len(test_failures)
        sample = test_failures[0]["description"][:100]
        hints.append(f"{failure_count} test failure(s): {sample}")

    # Reports written but no code changes
    if _agents_only_reports and not _agents_wrote_code and not failed_agents:
        report_agents = ", ".join(_agents_only_reports)
        hints.append(f"Reports only (no code changes) from: {report_agents}")

    # Code written but not reviewed/tested
    roles_this_round = set(successful_agents)
    if _agents_wrote_code or has_file_changes:
        missing_quality = []
        if "reviewer" not in roles_this_round and "reviewer" not in failed_agents:
            missing_quality.append("code review")
        if "tester" not in roles_this_round and "tester" not in failed_agents:
            missing_quality.append("testing")
        if missing_quality:
            hints.append(f"Code changed but missing: {', '.join(missing_quality)}")

    # Blocked/followup items
    blocked_findings = [f for f in _findings if f["type"] == "blocked"]
    followup_findings = [f for f in _findings if f["type"] == "followup"]
    for bf in blocked_findings:
        hints.append(f"{bf['agent']} BLOCKED: {bf['description'][:120]}")
    for ff in followup_findings:
        hints.append(f"{ff['agent']} NEEDS FOLLOWUP: {ff['description'][:120]}")

    # Decision section
    if hints:
        parts.append("<hints>")
        for hint_text in hints:
            parts.append(f"  - {hint_text}")
        parts.append("</hints>")
        parts.append(
            "<instruction>\n"
            "Based on the hints above, create <delegate> blocks for the next actions.\n"
            "Choose the right agent and write a specific task description for each.\n"
            "Do NOT say TASK_COMPLETE while there are unresolved issues.\n"
            "</instruction>"
        )
    else:
        all_success = not has_errors
        has_review = "reviewer" in mgr._agents_used
        has_tests = "tester" in mgr._agents_used
        code_changed = bool(_agents_wrote_code) or has_file_changes

        if all_success and has_review and has_tests and code_changed:
            parts.append(
                "<decision>\n"
                "All agents succeeded, code was reviewed and tested.\n"
                "If the original task is fully addressed, respond with TASK_COMPLETE.\n"
                "If there's more work needed, create <delegate> blocks for the next phase.\n"
                "</decision>"
            )
        elif all_success and code_changed:
            missing = []
            if not has_review:
                missing.append("code review (reviewer)")
            if not has_tests:
                missing.append("testing (tester)")
            parts.append(
                f"<decision>\n"
                f"Code was changed but still needs: {', '.join(missing)}.\n"
                f"Delegate the missing steps before TASK_COMPLETE.\n"
                f"</decision>"
            )
        elif not code_changed and all_success:
            parts.append(
                "<decision>\n"
                "No code changes detected. Either:\n"
                "A) The task doesn't require code changes -> TASK_COMPLETE if done\n"
                "B) Agents didn't do the work -> delegate with more specific instructions\n"
                "</decision>"
            )
        else:
            parts.append(
                "<decision>\n"
                "Review the results above and decide what to do next.\n"
                "Create <delegate> blocks for any remaining work.\n"
                "</decision>"
            )

    # Inject stuck escalation hint if detected
    hint = mgr._stuck_escalation_hint
    if hint:
        parts.append(f"\n{'=' * 50}")
        parts.append(hint)
        parts.append(f"{'=' * 50}")
        mgr._stuck_escalation_hint = ""

    return "\n".join(parts)


# ── Auto-evaluation ───────────────────────────────────────────────────


async def auto_evaluate(
    mgr, sub_results: dict[str, list[SDKResponse]], round_num: int
) -> dict | None:
    """Run automatic evaluation after a round of sub-agent work.

    Detects if code was changed, runs tests/build if available,
    and optionally auto-retries the developer if tests fail.
    """
    if "developer" not in sub_results:
        return None

    file_changes = await detect_file_changes(mgr)
    if not file_changes or "(no file" in file_changes:
        return None

    test_output = await run_project_tests(mgr)
    if test_output is None:
        return {
            "summary": "No test framework detected — skipping auto-evaluation.",
            "tests_passed": None,
            "auto_fixed": False,
            "updated_results": sub_results,
        }

    test_passed = test_output["passed"]
    test_summary = test_output["output"][:1500]

    if test_passed:
        return {
            "summary": f"✅ Auto-evaluation: Tests PASSED\n{test_summary[:500]}",
            "tests_passed": True,
            "auto_fixed": False,
            "updated_results": sub_results,
        }

    # Tests failed — auto-retry developer
    await mgr._notify(
        f"🔄 Auto-evaluator detected test failures in round {round_num}. "
        f"Sending developer back to fix..."
    )
    logger.info(f"[{mgr.project_id}] Auto-evaluator: tests failed, auto-retrying developer")

    fix_prompt = (
        f"Project: {mgr.project_name}\n"
        f"Working directory: {mgr.project_dir}\n\n"
        f"URGENT: Tests are failing after your changes. Fix the code to make tests pass.\n\n"
        f"Test output:\n```\n{test_summary}\n```\n\n"
        f"Instructions:\n"
        f"1. Read the test output carefully to understand what's failing\n"
        f"2. Fix the SOURCE CODE (not the tests) to resolve the failures\n"
        f"3. Run the tests again to verify your fix works\n"
        f"4. Report what you changed\n"
    )

    try:
        from orch_context import accumulate_context

        fix_response = await mgr._query_agent("developer", fix_prompt)
        await accumulate_context(mgr, "developer", "Auto-fix: resolve test failures", fix_response)

        retest = await run_project_tests(mgr)
        retest_passed = retest["passed"] if retest else False
        retest_summary = retest["output"][:500] if retest else "(retest failed)"

        updated = dict(sub_results)
        updated.setdefault("developer", []).append(fix_response)

        return {
            "summary": (
                f"🔄 Auto-evaluator: Tests failed after round {round_num}.\n"
                f"Developer was auto-retried to fix.\n"
                f"Retest result: {'PASSED ✅' if retest_passed else 'STILL FAILING ❌'}\n"
                f"Retest output: {retest_summary}"
            ),
            "tests_passed": retest_passed,
            "auto_fixed": True,
            "updated_results": updated,
        }
    except Exception as e:
        logger.warning(f"[{mgr.project_id}] Auto-fix failed: {e}")
        return {
            "summary": f"❌ Auto-evaluator: Tests failed. Auto-fix attempt also failed: {str(e)[:200]}",
            "tests_passed": False,
            "auto_fixed": False,
            "updated_results": sub_results,
        }


async def run_project_tests(mgr) -> dict | None:
    """Detect and run the project's test suite."""
    project = Path(mgr.project_dir)

    test_commands = []

    if (
        (project / "pytest.ini").exists()
        or (project / "setup.cfg").exists()
        or (project / "pyproject.toml").exists()
        or (project / "tests").is_dir()
        or (project / "test").is_dir()
    ):
        test_commands.append(
            [
                "python3",
                "-m",
                "pytest",
                "--tb=short",
                "-q",
                "--no-header",
                f"--timeout={PYTEST_TIMEOUT}",
            ]
        )

    if (project / "package.json").exists():
        try:
            pkg = json.loads((project / "package.json").read_text())
            scripts = pkg.get("scripts", {})
            if "test" in scripts and scripts["test"] != 'echo "Error: no test specified" && exit 1':
                test_commands.append(["npm", "test", "--", "--watchAll=false"])
        except Exception as _exc:
            logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)

    if not test_commands:
        logger.debug(
            f"[{mgr.project_id}] run_project_tests: no test framework detected "
            f"(no pytest.ini/setup.cfg/pyproject.toml/tests/ and no npm test script)"
        )
        return None

    failed_commands: list[str] = []
    for cmd in test_commands:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=mgr.project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "CI": "true", "FORCE_COLOR": "0"},
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=SUBPROCESS_LONG_TIMEOUT)
            output = stdout.decode("utf-8", errors="replace")
            passed = proc.returncode == 0
            return {"passed": passed, "output": output}
        except TimeoutError:
            return {
                "passed": False,
                "output": f"Tests timed out after 120s (command: {' '.join(cmd)})",
            }
        except FileNotFoundError:
            failed_commands.append(cmd[0])
            continue
        except Exception as e:
            return {"passed": False, "output": f"Test execution error: {e!s}"}

    if failed_commands:
        logger.warning(
            f"[{mgr.project_id}] run_project_tests: all test commands failed to launch "
            f"(not found: {failed_commands})"
        )
    return None
