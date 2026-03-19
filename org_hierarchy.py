"""
Organizational Hierarchy — Corporate structure for AI engineering teams.

Each project gets a virtual C-suite and management layer that maps to
the underlying agent roles. This provides:

1. Clear chain of command — who reports to whom
2. Decision authority — who can approve/reject changes
3. Escalation paths — what happens when an agent is blocked
4. Resource allocation — budget and priority per department

The hierarchy is stored in project config_json["org_chart"] and is
used by the PM agent to structure task delegation.

Architecture:
    CEO (Orchestrator)
    ├── CTO (PM + Memory)
    │   ├── VP Engineering
    │   │   ├── Frontend Lead → frontend_developer
    │   │   ├── Backend Lead → backend_developer
    │   │   └── Database Lead → database_expert
    │   ├── VP Quality
    │   │   ├── QA Lead → test_engineer
    │   │   ├── Security Lead → security_auditor
    │   │   └── Code Review Lead → reviewer
    │   └── VP Research
    │       ├── Research Lead → researcher
    │       └── UX Lead → ux_critic
    └── VP Operations
        └── DevOps Lead → devops
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Executive Titles
# ---------------------------------------------------------------------------


class ExecutiveTitle(StrEnum):
    """C-suite and VP-level titles for the org chart."""

    CEO = "ceo"
    CTO = "cto"
    VP_ENGINEERING = "vp_engineering"
    VP_QUALITY = "vp_quality"
    VP_RESEARCH = "vp_research"
    VP_OPERATIONS = "vp_operations"
    LEAD = "lead"


# ---------------------------------------------------------------------------
# Org Node — single position in the hierarchy
# ---------------------------------------------------------------------------


@dataclass
class OrgNode:
    """A single position in the organizational hierarchy."""

    title: str  # e.g. "CEO", "VP Engineering"
    executive_title: ExecutiveTitle  # enum value
    agent_role: str | None = None  # mapped agent role (e.g. "orchestrator")
    responsibilities: list[str] = field(default_factory=list)
    reports_to: str | None = None  # parent executive_title
    direct_reports: list[str] = field(default_factory=list)  # child executive_titles
    decision_authority: list[str] = field(default_factory=list)
    budget_pct: float = 0.0  # % of total project budget

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["executive_title"] = self.executive_title.value
        return d


# ---------------------------------------------------------------------------
# Default Org Chart
# ---------------------------------------------------------------------------

DEFAULT_ORG_CHART: dict[str, OrgNode] = {
    # ── C-Suite ──────────────────────────────────────────────────────
    ExecutiveTitle.CEO.value: OrgNode(
        title="Chief Executive Officer",
        executive_title=ExecutiveTitle.CEO,
        agent_role="orchestrator",
        responsibilities=[
            "Overall project vision and strategy",
            "Final approval on architecture decisions",
            "Resource allocation across departments",
            "Escalation endpoint for blocked tasks",
            "Go/no-go decisions for releases",
        ],
        reports_to=None,
        direct_reports=[
            ExecutiveTitle.CTO.value,
            ExecutiveTitle.VP_OPERATIONS.value,
        ],
        decision_authority=[
            "project_scope",
            "release_approval",
            "budget_override",
            "agent_reassignment",
        ],
        budget_pct=5.0,
    ),
    ExecutiveTitle.CTO.value: OrgNode(
        title="Chief Technology Officer",
        executive_title=ExecutiveTitle.CTO,
        agent_role="pm",
        responsibilities=[
            "Technical architecture and design decisions",
            "Task decomposition and sprint planning",
            "Technology stack selection",
            "Cross-team dependency management",
            "Technical debt prioritization",
        ],
        reports_to=ExecutiveTitle.CEO.value,
        direct_reports=[
            ExecutiveTitle.VP_ENGINEERING.value,
            ExecutiveTitle.VP_QUALITY.value,
            ExecutiveTitle.VP_RESEARCH.value,
        ],
        decision_authority=[
            "architecture",
            "technology_choice",
            "task_priority",
            "dependency_resolution",
        ],
        budget_pct=10.0,
    ),
    # ── VP Level ─────────────────────────────────────────────────────
    ExecutiveTitle.VP_ENGINEERING.value: OrgNode(
        title="VP of Engineering",
        executive_title=ExecutiveTitle.VP_ENGINEERING,
        agent_role="memory",
        responsibilities=[
            "Oversee all code-writing agents",
            "Ensure code quality standards",
            "Manage technical debt",
            "Coordinate frontend/backend/database work",
            "Maintain project memory and context",
        ],
        reports_to=ExecutiveTitle.CTO.value,
        direct_reports=["frontend_developer", "backend_developer", "database_expert"],
        decision_authority=[
            "code_standards",
            "refactoring_priority",
            "merge_approval",
        ],
        budget_pct=35.0,
    ),
    ExecutiveTitle.VP_QUALITY.value: OrgNode(
        title="VP of Quality Assurance",
        executive_title=ExecutiveTitle.VP_QUALITY,
        agent_role=None,  # Virtual — coordinates quality agents
        responsibilities=[
            "Oversee all quality and review agents",
            "Define quality gates and standards",
            "Ensure test coverage targets",
            "Coordinate security audits",
            "Sign off on code reviews",
        ],
        reports_to=ExecutiveTitle.CTO.value,
        direct_reports=["test_engineer", "security_auditor", "reviewer"],
        decision_authority=[
            "quality_gate",
            "test_coverage_threshold",
            "security_policy",
            "review_approval",
        ],
        budget_pct=25.0,
    ),
    ExecutiveTitle.VP_RESEARCH.value: OrgNode(
        title="VP of Research & Design",
        executive_title=ExecutiveTitle.VP_RESEARCH,
        agent_role=None,  # Virtual — coordinates research agents
        responsibilities=[
            "Oversee research and UX agents",
            "Competitive analysis and market research",
            "UX standards and accessibility",
            "Documentation quality",
        ],
        reports_to=ExecutiveTitle.CTO.value,
        direct_reports=["researcher", "ux_critic"],
        decision_authority=[
            "research_scope",
            "ux_standards",
            "documentation_quality",
        ],
        budget_pct=10.0,
    ),
    ExecutiveTitle.VP_OPERATIONS.value: OrgNode(
        title="VP of Operations",
        executive_title=ExecutiveTitle.VP_OPERATIONS,
        agent_role=None,  # Virtual — coordinates ops agents
        responsibilities=[
            "Oversee deployment and infrastructure",
            "CI/CD pipeline management",
            "Environment configuration",
            "Monitoring and alerting",
        ],
        reports_to=ExecutiveTitle.CEO.value,
        direct_reports=["devops"],
        decision_authority=[
            "deployment_approval",
            "infrastructure_changes",
            "environment_config",
        ],
        budget_pct=15.0,
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_default_org_chart() -> dict[str, Any]:
    """Return the default org chart as a JSON-serializable dict."""
    return {k: v.to_dict() for k, v in DEFAULT_ORG_CHART.items()}


def get_org_chart_for_project(config: dict[str, Any] | None) -> dict[str, Any]:
    """Extract org chart from project config, falling back to defaults."""
    if config and "org_chart" in config:
        return config["org_chart"]
    return get_default_org_chart()


def get_reporting_chain(role: str) -> list[str]:
    """Return the chain of command for a given agent role (bottom-up).

    Example: get_reporting_chain("frontend_developer")
    → ["vp_engineering", "cto", "ceo"]
    """
    # Find which executive has this role as a direct report
    chain: list[str] = []
    current = None

    for exec_title, node in DEFAULT_ORG_CHART.items():
        if role in node.direct_reports or node.agent_role == role:
            current = exec_title
            break

    # Walk up the tree
    while current:
        chain.append(current)
        node = DEFAULT_ORG_CHART.get(current)
        if node and node.reports_to:
            current = node.reports_to
        else:
            break

    return chain


def get_agents_under(executive_title: str) -> list[str]:
    """Return all agent roles that report (directly or indirectly) to an executive."""
    node = DEFAULT_ORG_CHART.get(executive_title)
    if not node:
        return []

    agents: list[str] = []
    if node.agent_role:
        agents.append(node.agent_role)

    for report in node.direct_reports:
        # Check if it's an executive title (recurse) or an agent role (leaf)
        if report in DEFAULT_ORG_CHART:
            agents.extend(get_agents_under(report))
        else:
            agents.append(report)

    return agents


def get_escalation_path(agent_role: str) -> dict[str, Any]:
    """Return the escalation path for a blocked agent.

    Returns:
        {
            "agent": "frontend_developer",
            "escalation_chain": ["vp_engineering", "cto", "ceo"],
            "first_escalation": "vp_engineering",
            "final_authority": "ceo"
        }
    """
    chain = get_reporting_chain(agent_role)
    return {
        "agent": agent_role,
        "escalation_chain": chain,
        "first_escalation": chain[0] if chain else None,
        "final_authority": chain[-1] if chain else None,
    }


def build_org_prompt_section() -> str:
    """Build an XML section describing the org hierarchy for agent prompts.

    This is injected into the PM and Orchestrator system prompts so they
    understand the corporate structure and chain of command.
    """
    lines = [
        "<organizational_hierarchy>",
        "The project operates with a corporate management structure.",
        "Each agent has a reporting chain and escalation path.",
        "",
        "Org Chart:",
    ]

    for _exec_title, node in DEFAULT_ORG_CHART.items():
        indent = "  " if node.reports_to else ""
        agent_str = f" (→ {node.agent_role})" if node.agent_role else ""
        lines.append(f"{indent}{node.title}{agent_str}")
        if node.direct_reports:
            for report in node.direct_reports:
                sub_node = DEFAULT_ORG_CHART.get(report)
                if sub_node:
                    lines.append(f"    ├── {sub_node.title}")
                else:
                    lines.append(f"    ├── {report}")
        lines.append("")

    lines.extend(
        [
            "Decision Authority Rules:",
            "- Architecture decisions: CTO approval required",
            "- Code merges: VP Engineering or VP Quality must approve",
            "- Deployment: VP Operations must approve",
            "- Scope changes: CEO approval required",
            "- Security exceptions: VP Quality + CEO must both approve",
            "",
            "Escalation Protocol:",
            "- Agent blocked → escalate to direct manager (VP level)",
            "- VP cannot resolve → escalate to CTO",
            "- CTO cannot resolve → escalate to CEO",
            "- CEO makes final decision or requests user input",
            "</organizational_hierarchy>",
        ]
    )

    return "\n".join(lines)
