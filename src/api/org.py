"""
Organization Hierarchy API — REST endpoints for project org charts.

Provides read/update access to the organizational hierarchy stored
in each project's config_json["org_chart"].
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

# Add project root to path for imports
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from org_hierarchy import (
    get_agents_under,
    get_default_org_chart,
    get_escalation_path,
    get_org_chart_for_project,
)
from src.dependencies import get_project_manager
from src.projects.project_manager import ProjectManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["organization"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class OrgChartResponse(BaseModel):
    project_id: str
    org_chart: dict[str, Any]


class EscalationResponse(BaseModel):
    agent: str
    escalation_chain: list[str]
    first_escalation: str | None
    final_authority: str | None


class AgentsUnderResponse(BaseModel):
    executive: str
    agents: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{project_id}/org-chart", response_model=OrgChartResponse)
async def get_project_org_chart(
    project_id: str,
    mgr: ProjectManager = Depends(get_project_manager),
):
    """Return the organizational hierarchy for a project."""
    project = await mgr.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    config = project.get("config") or {}
    org_chart = get_org_chart_for_project(config)

    return OrgChartResponse(project_id=project_id, org_chart=org_chart)


@router.put("/{project_id}/org-chart", response_model=OrgChartResponse)
async def update_project_org_chart(
    project_id: str,
    org_chart: dict[str, Any],
    mgr: ProjectManager = Depends(get_project_manager),
):
    """Update the organizational hierarchy for a project."""
    project = await mgr.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    config = project.get("config") or {}
    config["org_chart"] = org_chart
    await mgr.update_project(project_id, config=config)

    return OrgChartResponse(project_id=project_id, org_chart=org_chart)


@router.get("/{project_id}/org-chart/escalation/{agent_role}", response_model=EscalationResponse)
async def get_agent_escalation(
    project_id: str,
    agent_role: str,
):
    """Return the escalation path for a specific agent role."""
    path = get_escalation_path(agent_role)
    return EscalationResponse(**path)


@router.get("/{project_id}/org-chart/agents-under/{executive}", response_model=AgentsUnderResponse)
async def get_agents_under_executive(
    project_id: str,
    executive: str,
):
    """Return all agent roles that report to an executive."""
    agents = get_agents_under(executive)
    return AgentsUnderResponse(executive=executive, agents=agents)


@router.get("/org-chart/default", response_model=dict)
async def get_default_org():
    """Return the default org chart template."""
    return get_default_org_chart()
