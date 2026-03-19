"""REST API router for multi-project CRUD.

Endpoints
---------
POST   /api/projects
    Create a new project.  Returns 201 with the full project record.

GET    /api/projects
    List all projects (paginated, newest-first).

GET    /api/projects/{project_id}
    Return a single project by its UUID.

PATCH  /api/projects/{project_id}
    Partially update a project's name and/or config.

DELETE /api/projects/{project_id}
    Hard-delete a project and cascade all child records
    (conversations → messages → agent_actions, memory).

All errors follow the RFC 7807 Problem Detail format::

    {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "Project 550e8400-… not found."
    }

Project IDs are UUIDs (not integers) to prevent enumeration attacks.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from src.dependencies import get_project_manager
from src.projects.project_manager import ProjectManager

logger = logging.getLogger(__name__)

projects_router = APIRouter(tags=["projects"])

# ---------------------------------------------------------------------------
# UUID validation helper
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_valid_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


# ---------------------------------------------------------------------------
# RFC 7807 error helper
# ---------------------------------------------------------------------------

_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    404: "Not Found",
    409: "Conflict",
    422: "Unprocessable Content",
    500: "Internal Server Error",
}


def _problem(status: int, detail: str) -> JSONResponse:
    """Return an RFC 7807 Problem Detail response."""
    return JSONResponse(
        {
            "type": "about:blank",
            "title": _TITLES.get(status, "Error"),
            "status": status,
            "detail": detail,
        },
        status_code=status,
    )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ProjectResponse(BaseModel):
    """Full project record returned by all endpoints."""

    id: str = Field(
        description="UUID project identifier.",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    name: str = Field(
        description="Human-readable project display name.",
        examples=["My API Project"],
    )
    config: dict = Field(
        default_factory=dict,
        description=(
            "Project-level configuration blob. "
            "Suggested keys: budget_usd, default_model, tags, description."
        ),
        examples=[{"budget_usd": 50, "default_model": "claude-opus-4-6"}],
    )
    created_at: str | None = Field(
        description="ISO-8601 UTC creation timestamp.",
        examples=["2026-03-11T10:00:00+00:00"],
    )
    updated_at: str | None = Field(
        description="ISO-8601 UTC last-modified timestamp.",
        examples=["2026-03-11T10:05:00+00:00"],
    )

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "name": "My API Project",
                "config": {"budget_usd": 50, "default_model": "claude-opus-4-6"},
                "created_at": "2026-03-11T10:00:00+00:00",
                "updated_at": "2026-03-11T10:05:00+00:00",
            }
        },
    }


class ProjectListResponse(BaseModel):
    """Paginated list of projects."""

    projects: list[ProjectResponse]
    total: int = Field(description="Number of projects in this page.")
    limit: int
    offset: int
    isolation_mode: str = Field(
        description="Active isolation mode: 'row_level' or 'per_db'.",
        examples=["row_level"],
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "projects": [],
                "total": 0,
                "limit": 100,
                "offset": 0,
                "isolation_mode": "row_level",
            }
        }
    }


class CreateProjectRequest(BaseModel):
    """Request body for creating a new project.

    Only ``name`` is required.  ``config`` may be omitted and defaults to
    an empty dict.  ``project_id`` may be supplied by the caller for
    idempotent creation (e.g. client-generated UUIDs); if omitted the server
    generates one.
    """

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable display name.",
        examples=["My API Project"],
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional project-level configuration blob.",
        examples=[{"budget_usd": 50}],
    )
    project_id: str | None = Field(
        default=None,
        description=(
            "Optional caller-supplied UUID.  Useful for idempotent creation. "
            "Server generates a UUID4 if not provided."
        ),
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be blank.")
        return stripped

    @field_validator("project_id")
    @classmethod
    def _validate_project_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _is_valid_uuid(v):
            raise ValueError(f"project_id must be a valid UUID4; got {v!r}.")
        return v

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "My API Project",
                "config": {"budget_usd": 50, "default_model": "claude-opus-4-6"},
            }
        }
    }


class UpdateProjectRequest(BaseModel):
    """Request body for PATCH /api/projects/{project_id}.

    All fields are optional — only supplied fields are updated.
    """

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New display name.",
        examples=["Renamed Project"],
    )
    config: dict[str, Any] | None = Field(
        default=None,
        description="Replacement config blob (full replace, not merge).",
        examples=[{"budget_usd": 100}],
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be blank.")
        return stripped

    model_config = {
        "json_schema_extra": {"example": {"name": "Renamed Project", "config": {"budget_usd": 100}}}
    }


# ---------------------------------------------------------------------------
# POST /api/projects — create
# ---------------------------------------------------------------------------


@projects_router.post(
    "/api/projects",
    response_model=ProjectResponse,
    status_code=201,
    summary="Create a new project",
    description=(
        "Creates a new project with a server-generated (or caller-supplied) UUID. "
        "Returns the full project record.  Idempotent if the same ``project_id`` "
        "is supplied on repeated calls and the project already exists."
    ),
    responses={
        201: {"description": "Project created successfully."},
        400: {"description": "Invalid request body (name blank, UUID malformed, etc.)."},
        409: {"description": "A project with the supplied project_id already exists."},
        500: {"description": "Internal server error — check server logs."},
    },
)
async def create_project(
    req: CreateProjectRequest,
    mgr: ProjectManager = Depends(get_project_manager),
):
    """Create a new project.

    Returns 201 with the created project.  If the caller supplies a
    ``project_id`` and that project already exists, returns 409 Conflict.
    """
    # If caller supplied a project_id, check for conflict first.
    if req.project_id is not None:
        existing = await mgr.get_project(req.project_id)
        if existing is not None:
            return _problem(
                409,
                f"Project {req.project_id!r} already exists.  "
                "Use PATCH /api/projects/{project_id} to update it.",
            )

    try:
        project = await mgr.create_project(
            req.name,
            config=req.config,
            project_id=req.project_id,
        )
        return ProjectResponse(**project)
    except ValueError as exc:
        return _problem(400, str(exc))
    except Exception:
        logger.error("POST /api/projects failed", exc_info=True)
        return _problem(500, "Failed to create project. Check server logs.")


# ---------------------------------------------------------------------------
# GET /api/projects — list
# ---------------------------------------------------------------------------


@projects_router.get(
    "/api/projects",
    response_model=ProjectListResponse,
    summary="List all projects",
    description=(
        "Returns all projects sorted by creation time (newest first). "
        "Supports pagination via ``limit`` and ``offset``."
    ),
    responses={
        200: {"description": "Paginated project list."},
        500: {"description": "Internal server error."},
    },
)
async def list_projects(
    limit: int = Query(default=100, ge=1, le=500, description="Max results per page."),
    offset: int = Query(default=0, ge=0, description="Pagination offset."),
    mgr: ProjectManager = Depends(get_project_manager),
):
    """Return all projects (newest first) with pagination."""
    try:
        projects = await mgr.list_projects(limit=limit, offset=offset)
        return ProjectListResponse(
            projects=[ProjectResponse(**p) for p in projects],
            total=len(projects),
            limit=limit,
            offset=offset,
            isolation_mode="per_db" if mgr.is_per_db_mode else "row_level",
        )
    except Exception:
        logger.error("GET /api/projects failed", exc_info=True)
        return _problem(500, "Failed to list projects. Check server logs.")


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id} — detail
# ---------------------------------------------------------------------------


@projects_router.get(
    "/api/projects/{project_id}",
    response_model=ProjectResponse,
    summary="Get a project by UUID",
    description="Returns the full project record for the given UUID.",
    responses={
        200: {"description": "Project found."},
        400: {"description": "project_id is not a valid UUID."},
        404: {"description": "Project not found."},
        500: {"description": "Internal server error."},
    },
)
async def get_project(
    project_id: str,
    mgr: ProjectManager = Depends(get_project_manager),
):
    """Return a single project by its UUID."""
    if not _is_valid_uuid(project_id):
        return _problem(
            400,
            f"project_id must be a valid UUID; got {project_id!r}.",
        )

    try:
        project = await mgr.get_project(project_id)
        if project is None:
            return _problem(404, f"Project {project_id!r} not found.")
        return ProjectResponse(**project)
    except Exception:
        logger.error("GET /api/projects/%s failed", project_id, exc_info=True)
        return _problem(500, "Failed to retrieve project. Check server logs.")


# ---------------------------------------------------------------------------
# PATCH /api/projects/{project_id} — partial update
# ---------------------------------------------------------------------------


@projects_router.patch(
    "/api/projects/{project_id}",
    response_model=ProjectResponse,
    summary="Update a project (partial)",
    description=(
        "Partially update a project's ``name`` and/or ``config``. "
        "Only supplied fields are changed; omitted fields keep their current values. "
        "``config`` is replaced in full (not merged)."
    ),
    responses={
        200: {"description": "Project updated."},
        400: {"description": "Invalid project_id format or request body."},
        404: {"description": "Project not found."},
        500: {"description": "Internal server error."},
    },
)
async def update_project(
    project_id: str,
    req: UpdateProjectRequest,
    mgr: ProjectManager = Depends(get_project_manager),
):
    """Partially update a project (name and/or config)."""
    if not _is_valid_uuid(project_id):
        return _problem(
            400,
            f"project_id must be a valid UUID; got {project_id!r}.",
        )

    if req.name is None and req.config is None:
        return _problem(400, "At least one of 'name' or 'config' must be provided.")

    try:
        updated = await mgr.update_project(
            project_id,
            name=req.name,
            config=req.config,
        )
        if updated is None:
            return _problem(404, f"Project {project_id!r} not found.")
        return ProjectResponse(**updated)
    except ValueError as exc:
        return _problem(400, str(exc))
    except Exception:
        logger.error("PATCH /api/projects/%s failed", project_id, exc_info=True)
        return _problem(500, "Failed to update project. Check server logs.")


# ---------------------------------------------------------------------------
# DELETE /api/projects/{project_id} — cascade delete
# ---------------------------------------------------------------------------


@projects_router.delete(
    "/api/projects/{project_id}",
    status_code=200,
    summary="Delete a project (cascade)",
    description=(
        "Hard-deletes a project and ALL its associated data: "
        "conversations, messages, agent_actions, and memory entries. "
        "In ``per_db`` mode the project's dedicated SQLite file is also removed. "
        "This operation is irreversible."
    ),
    responses={
        200: {"description": "Project deleted."},
        400: {"description": "project_id is not a valid UUID."},
        404: {"description": "Project not found."},
        500: {"description": "Internal server error."},
    },
)
async def delete_project(
    project_id: str,
    mgr: ProjectManager = Depends(get_project_manager),
):
    """Delete a project and cascade all child records.

    Idempotent in the sense that re-sending the DELETE after success
    returns 404 (the project is gone) rather than an error condition.
    The caller should treat 404 on a DELETE as a success if they are
    retrying after a network failure.
    """
    if not _is_valid_uuid(project_id):
        return _problem(
            400,
            f"project_id must be a valid UUID; got {project_id!r}.",
        )

    try:
        logger.info("DELETE /api/projects/%s — starting cascade delete", project_id)
        deleted = await mgr.delete_project(project_id)
        if not deleted:
            logger.info("DELETE /api/projects/%s — not found (already deleted?)", project_id)
            return _problem(404, f"Project {project_id!r} not found.")
        logger.info("DELETE /api/projects/%s — cascade delete completed successfully", project_id)
        return {
            "ok": True,
            "project_id": project_id,
            "deleted": True,
            "message": (
                "Project and all associated conversations, messages, "
                "agent_actions, and memory entries have been permanently deleted."
            ),
        }
    except Exception:
        logger.error("DELETE /api/projects/%s failed", project_id, exc_info=True)
        return _problem(500, "Failed to delete project. Check server logs.")
