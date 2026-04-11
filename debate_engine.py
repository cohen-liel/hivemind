"""Debate Engine — structured multi-perspective review for critical tasks.

Before executing high-stakes tasks (architecture, security, database schema),
this module runs a structured debate between the assigned agent and a
challenger agent with a different specialization.  A judge then picks the
stronger approach.

The debate uses the same ``isolated_query`` function that the DAG executor
uses, so it runs through the real Claude SDK with no extra dependencies.

Integration point:
    orchestrator._run_dag_session — after PM creates the TaskGraph but
    before DAG execution, call ``enrich_graph_with_debates(graph)`` to
    annotate critical tasks with debate results.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from contracts import AgentRole, TaskInput

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────


class DebateVerdict(StrEnum):
    ORIGINAL = "original"
    CHALLENGER = "challenger"
    MERGED = "merged"


# Roles that trigger a debate (high-impact decisions)
DEBATE_ELIGIBLE_ROLES: set[AgentRole] = {
    AgentRole.DATABASE_EXPERT,
    AgentRole.SECURITY_AUDITOR,
    AgentRole.DEVOPS,
}

# Keywords in task goals that trigger debate regardless of role.
# Intentionally strict — only multi-word phrases that indicate
# high-stakes architectural decisions, not common single words.
DEBATE_KEYWORDS: list[str] = [
    "database migration",
    "schema migration",
    "security architecture",
    "infrastructure design",
    "api design",
]

# Which role challenges which
CHALLENGER_MAP: dict[AgentRole, AgentRole] = {
    AgentRole.DATABASE_EXPERT: AgentRole.BACKEND_DEVELOPER,
    AgentRole.SECURITY_AUDITOR: AgentRole.BACKEND_DEVELOPER,
    AgentRole.DEVOPS: AgentRole.BACKEND_DEVELOPER,
    AgentRole.BACKEND_DEVELOPER: AgentRole.SECURITY_AUDITOR,
    AgentRole.FRONTEND_DEVELOPER: AgentRole.REVIEWER,
}

# Default max debate rounds (1 round = proposer + challenger + judge = 3 SDK calls)
DEFAULT_MAX_ROUNDS = 1

# Environment variable to enable the debate engine (opt-in, off by default)
DEBATE_ENABLED_ENV = "HIVEMIND_DEBATE_ENABLED"


@dataclass
class DebateRound:
    """One round of debate."""

    round_num: int
    proposer_argument: str
    challenger_argument: str


@dataclass
class DebateResult:
    """Complete result of a debate."""

    task_id: str
    proposer_role: AgentRole
    challenger_role: AgentRole
    rounds: list[DebateRound]
    verdict: DebateVerdict
    verdict_reasoning: str
    merged_approach: str  # empty if verdict != MERGED
    cost_turns: int = 0  # total SDK turns used


@dataclass
class DebateEngine:
    """Manages structured debates between agents for critical tasks.

    The engine is stateless per-debate — each debate is independent.
    It tracks history for reporting purposes only.
    """

    max_rounds: int = DEFAULT_MAX_ROUNDS
    eligible_roles: set[AgentRole] = field(default_factory=lambda: set(DEBATE_ELIGIBLE_ROLES))
    keywords: list[str] = field(default_factory=lambda: list(DEBATE_KEYWORDS))
    history: list[DebateResult] = field(default_factory=list)

    def should_debate(self, task: TaskInput) -> bool:
        """Decide whether a task warrants a debate.

        Returns True if:
        0. The debate engine is enabled via HIVEMIND_DEBATE_ENABLED=true, AND
        1. The task role is in DEBATE_ELIGIBLE_ROLES, OR
        2. The task goal contains debate keywords.
        """
        import os

        if os.getenv(DEBATE_ENABLED_ENV, "false").lower() not in ("1", "true", "yes"):
            return False

        if task.role in self.eligible_roles:
            return True

        goal_lower = task.goal.lower()
        return any(kw in goal_lower for kw in self.keywords)

    def get_challenger_role(self, task: TaskInput) -> AgentRole:
        """Return the challenger role for a given task."""
        return CHALLENGER_MAP.get(task.role, AgentRole.REVIEWER)

    async def run_debate(
        self,
        task: TaskInput,
        project_dir: str,
        sdk=None,
        context: str = "",
        on_stream: Callable | None = None,
    ) -> DebateResult:
        """Run a structured debate for a task.

        Uses isolated_query to call Claude agents for each perspective.
        The debate has three phases:
        1. Proposer presents approach
        2. Challenger critiques and presents alternative
        3. Judge evaluates and picks winner (or merges)

        Args:
            task: The task to debate
            project_dir: Project directory for SDK calls
            sdk: ClaudeSDKManager instance (passed to isolated_query)
            context: Additional context (e.g., architect review)

        Returns:
            DebateResult with verdict and merged approach
        """
        # Lazy import to avoid circular dependency
        import state
        from config import get_agent_turns
        from isolated_query import isolated_query
        from prompts import PROMPT_REGISTRY as SPECIALIST_PROMPTS

        def _resolve_system_prompt(role: str) -> str:
            """Resolve system prompt from built-in prompts or plugin registry."""
            prompt = SPECIALIST_PROMPTS.get(role)
            if prompt is not None:
                return prompt
            try:
                from plugin_registry import registry as _pr

                plugin = _pr.get(role)
                if plugin is not None:
                    return plugin.build_prompt()
            except ImportError:
                pass
            return "You are an expert software engineer."

        _sdk = sdk or state.sdk_client

        challenger_role = self.get_challenger_role(task)
        rounds: list[DebateRound] = []
        total_turns = 0

        for round_num in range(1, self.max_rounds + 1):
            # ── Proposer argues ──────────────────────────────────────
            proposer_prompt = (
                f"You are debating the best approach for this task.\n"
                f"Task: {task.goal}\n"
                f"Context: {context}\n"
            )
            if rounds:
                last = rounds[-1]
                proposer_prompt += (
                    f"\nThe challenger argued:\n{last.challenger_argument}\n"
                    f"\nRespond to their critique and strengthen your approach."
                )
            else:
                proposer_prompt += (
                    "\nPresent your proposed approach. Be specific about "
                    "implementation details, trade-offs, and risks."
                )

            proposer_response = await isolated_query(
                _sdk,
                prompt=proposer_prompt,
                system_prompt=_resolve_system_prompt(task.role),
                cwd=project_dir,
                max_turns=min(get_agent_turns(task.role) // 2, 5),
                max_budget_usd=1.0,
                on_stream=on_stream,
            )
            proposer_text = proposer_response.text if proposer_response else ""
            total_turns += proposer_response.num_turns if proposer_response else 0

            # ── Challenger argues ────────────────────────────────────
            challenger_prompt = (
                f"You are reviewing a proposed approach for this task.\n"
                f"Task: {task.goal}\n"
                f"Context: {context}\n"
                f"\nProposed approach:\n{proposer_text}\n"
                f"\nCritique this approach. Identify weaknesses, risks, and "
                f"propose a better alternative if you have one."
            )

            challenger_response = await isolated_query(
                _sdk,
                prompt=challenger_prompt,
                system_prompt=SPECIALIST_PROMPTS.get(
                    challenger_role.value, "You are an expert software engineer."
                ),
                cwd=project_dir,
                max_turns=min(get_agent_turns(challenger_role.value) // 2, 5),
                max_budget_usd=1.0,
                on_stream=on_stream,
            )
            challenger_text = challenger_response.text if challenger_response else ""
            total_turns += challenger_response.num_turns if challenger_response else 0

            rounds.append(
                DebateRound(
                    round_num=round_num,
                    proposer_argument=proposer_text,
                    challenger_argument=challenger_text,
                )
            )

        # ── Judge evaluates ──────────────────────────────────────────
        debate_transcript = ""
        for r in rounds:
            debate_transcript += (
                f"--- Round {r.round_num} ---\n"
                f"Proposer ({task.role}):\n{r.proposer_argument}\n\n"
                f"Challenger ({challenger_role.value}):\n{r.challenger_argument}\n\n"
            )

        judge_prompt = (
            f"You are judging a technical debate about this task:\n"
            f"Task: {task.goal}\n\n"
            f"Debate transcript:\n{debate_transcript}\n\n"
            f"Evaluate both approaches. Respond in this exact format:\n"
            f"VERDICT: [original|challenger|merged]\n"
            f"REASONING: [your reasoning]\n"
            f"APPROACH: [the winning or merged approach — be specific]"
        )

        judge_response = await isolated_query(
            _sdk,
            prompt=judge_prompt,
            system_prompt="You are a senior technical judge. Be objective and thorough.",
            cwd=project_dir,
            max_turns=3,
            max_budget_usd=1.0,
            on_stream=on_stream,
        )
        judge_text = judge_response.text if judge_response else ""
        total_turns += judge_response.num_turns if judge_response else 0

        # Parse verdict
        verdict, reasoning, approach = self._parse_verdict(judge_text)

        result = DebateResult(
            task_id=task.id,
            proposer_role=task.role,
            challenger_role=challenger_role,
            rounds=rounds,
            verdict=verdict,
            verdict_reasoning=reasoning,
            merged_approach=approach,
            cost_turns=total_turns,
        )
        self.history.append(result)

        logger.info(
            "[DebateEngine] task %s: verdict=%s (proposer=%s, challenger=%s, turns=%d)",
            task.id,
            verdict.value,
            task.role,
            challenger_role.value,
            total_turns,
        )
        return result

    def build_debate_context(self, result: DebateResult) -> str:
        """Convert a debate result into context to inject into the task prompt.

        This is appended to the task prompt before execution so the agent
        benefits from the debate insights.
        """
        lines = [
            "## Pre-execution Debate Summary",
            f"A debate was held between {result.proposer_role.value} and "
            f"{result.challenger_role.value}.",
            f"Verdict: **{result.verdict.value}**",
            f"Reasoning: {result.verdict_reasoning}",
        ]
        if result.merged_approach:
            lines.append(f"\nRecommended approach:\n{result.merged_approach}")
        return "\n".join(lines)

    def get_summary(self) -> dict[str, Any]:
        """Return a summary of all debates."""
        total = len(self.history)
        verdicts = {}
        for r in self.history:
            verdicts[r.verdict.value] = verdicts.get(r.verdict.value, 0) + 1
        return {
            "total_debates": total,
            "verdicts": verdicts,
            "total_turns_used": sum(r.cost_turns for r in self.history),
        }

    # ── Internal ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_verdict(text: str) -> tuple[DebateVerdict, str, str]:
        """Parse the judge response into verdict, reasoning, approach."""
        import re

        verdict = DebateVerdict.MERGED  # default
        reasoning = ""
        approach = ""

        # Extract VERDICT
        m = re.search(r"VERDICT:\s*(original|challenger|merged)", text, re.IGNORECASE)
        if m:
            v = m.group(1).lower()
            try:
                verdict = DebateVerdict(v)
            except ValueError:
                verdict = DebateVerdict.MERGED

        # Extract REASONING
        m = re.search(r"REASONING:\s*(.+?)(?=APPROACH:|$)", text, re.DOTALL | re.IGNORECASE)
        if m:
            reasoning = m.group(1).strip()

        # Extract APPROACH
        m = re.search(r"APPROACH:\s*(.+)", text, re.DOTALL | re.IGNORECASE)
        if m:
            approach = m.group(1).strip()

        return verdict, reasoning, approach
