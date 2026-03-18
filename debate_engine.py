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
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from contracts import AgentRole, TaskInput

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────

class DebateVerdict(str, Enum):
    ORIGINAL = "original"
    CHALLENGER = "challenger"
    MERGED = "merged"


# Roles that trigger a debate (high-impact decisions)
DEBATE_ELIGIBLE_ROLES: set[AgentRole] = {
    AgentRole.DATABASE_EXPERT,
    AgentRole.SECURITY_AUDITOR,
    AgentRole.DEVOPS,
}

# Keywords in task goals that trigger debate regardless of role
DEBATE_KEYWORDS: list[str] = [
    "architect", "schema", "migration", "security", "auth",
    "deploy", "infrastructure", "database design", "api design",
]

# Which role challenges which
CHALLENGER_MAP: dict[AgentRole, AgentRole] = {
    AgentRole.DATABASE_EXPERT: AgentRole.BACKEND_DEVELOPER,
    AgentRole.SECURITY_AUDITOR: AgentRole.BACKEND_DEVELOPER,
    AgentRole.DEVOPS: AgentRole.BACKEND_DEVELOPER,
    AgentRole.BACKEND_DEVELOPER: AgentRole.SECURITY_AUDITOR,
    AgentRole.FRONTEND_DEVELOPER: AgentRole.REVIEWER,
}

# Default max debate rounds
DEFAULT_MAX_ROUNDS = 2


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
    eligible_roles: set[AgentRole] = field(
        default_factory=lambda: set(DEBATE_ELIGIBLE_ROLES)
    )
    keywords: list[str] = field(
        default_factory=lambda: list(DEBATE_KEYWORDS)
    )
    history: list[DebateResult] = field(default_factory=list)

    def should_debate(self, task: TaskInput) -> bool:
        """Decide whether a task warrants a debate.

        Returns True if:
        1. The task role is in DEBATE_ELIGIBLE_ROLES, OR
        2. The task goal contains debate keywords.
        """
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
        context: str = "",
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
            context: Additional context (e.g., architect review)

        Returns:
            DebateResult with verdict and merged approach
        """
        # Lazy import to avoid circular dependency
        from isolated_query import isolated_query
        from config import SPECIALIST_PROMPTS, get_agent_turns

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
                prompt=proposer_prompt,
                system_prompt=SPECIALIST_PROMPTS.get(task.role, ""),
                project_dir=project_dir,
                max_turns=get_agent_turns(task.role) // 2,
                task_id=f"{task.id}_debate_r{round_num}_proposer",
            )
            proposer_text = proposer_response.text if proposer_response else ""
            total_turns += proposer_response.turns_used if proposer_response else 0

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
                prompt=challenger_prompt,
                system_prompt=SPECIALIST_PROMPTS.get(challenger_role, ""),
                project_dir=project_dir,
                max_turns=get_agent_turns(challenger_role) // 2,
                task_id=f"{task.id}_debate_r{round_num}_challenger",
            )
            challenger_text = challenger_response.text if challenger_response else ""
            total_turns += challenger_response.turns_used if challenger_response else 0

            rounds.append(DebateRound(
                round_num=round_num,
                proposer_argument=proposer_text,
                challenger_argument=challenger_text,
            ))

        # ── Judge evaluates ──────────────────────────────────────────
        debate_transcript = ""
        for r in rounds:
            debate_transcript += (
                f"--- Round {r.round_num} ---\n"
                f"Proposer ({task.role.value}):\n{r.proposer_argument}\n\n"
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
            prompt=judge_prompt,
            system_prompt="You are a senior technical judge. Be objective and thorough.",
            project_dir=project_dir,
            max_turns=3,
            task_id=f"{task.id}_debate_judge",
        )
        judge_text = judge_response.text if judge_response else ""
        total_turns += judge_response.turns_used if judge_response else 0

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
            task.id, verdict.value, task.role.value, challenger_role.value, total_turns,
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
