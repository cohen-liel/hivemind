"""Plugin management API endpoints.

Provides REST endpoints for listing, enabling, disabling, creating, and
deleting Hivemind custom agent-role plugins backed by :mod:`plugin_registry`.

Routes
------
GET    /api/plugins                   — list all discovered plugins
POST   /api/plugins                   — create a new plugin from form data
POST   /api/plugins/{name}/enable     — enable a plugin by role_name
POST   /api/plugins/{name}/disable    — disable a plugin by role_name
DELETE /api/plugins/{name}            — delete a plugin file and unload it
"""

from __future__ import annotations

import logging
import textwrap
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from plugin_registry import _ROLE_NAME_RE, registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PluginInfo(BaseModel):
    """Serialisable representation of a single plugin."""

    name: str = Field(
        ..., description="Unique role name of the plugin (e.g. 'documentation_writer')"
    )
    description: str = Field(..., description="First 120 chars of the plugin's system prompt")
    is_writer: bool = Field(..., description="True if the agent writes files; False for read-only")
    file_scope_patterns: list[str] = Field(
        ..., description="Glob patterns limiting which files this agent may access"
    )
    enabled: bool = Field(..., description="Whether the plugin is currently active")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "documentation_writer",
                "description": "You are a documentation writer …",
                "is_writer": True,
                "file_scope_patterns": ["**/*.py", "docs/**/*.md"],
                "enabled": True,
            }
        }


class PluginListResponse(BaseModel):
    plugins: list[PluginInfo]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metadata_to_info(meta_dict: dict[str, Any]) -> PluginInfo:
    """Convert a PluginMetadata.to_dict() result to a PluginInfo response model."""
    return PluginInfo(
        name=meta_dict["role_name"],
        description=meta_dict.get("system_prompt_preview", ""),
        is_writer=meta_dict["is_writer"],
        file_scope_patterns=meta_dict["file_scope_patterns"],
        enabled=meta_dict["enabled"],
    )


def _get_plugin_info(name: str) -> PluginInfo:
    """Return PluginInfo for *name* or raise 404."""
    meta = registry.get_metadata(name)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")
    return _metadata_to_info(meta.to_dict())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=PluginListResponse, summary="List all plugins")
async def list_plugins() -> PluginListResponse:
    """Return metadata for every discovered plugin regardless of enabled state."""
    raw = registry.list_all()
    plugins = [_metadata_to_info(item) for item in raw]
    logger.info("GET /api/plugins — returning %d plugin(s)", len(plugins))
    return PluginListResponse(plugins=plugins)


class CreatePluginRequest(BaseModel):
    """Request body for creating a new plugin."""

    role_name: str = Field(..., description="Unique role name (lowercase, underscores, 1-64 chars)")
    system_prompt: str = Field(..., min_length=10, description="System prompt for the agent")
    file_scope_patterns: list[str] = Field(
        default_factory=list, description="Glob patterns for file access"
    )
    is_writer: bool = Field(default=False, description="Whether the agent writes files")


_PLUGIN_TEMPLATE = textwrap.dedent('''\
    """Auto-generated Hivemind plugin: {role_name}."""

    from __future__ import annotations

    from plugin_registry import PluginBase


    class {class_name}(PluginBase):
        """Custom agent role: {role_name}."""

        @property
        def role_name(self) -> str:
            return {role_name_repr}

        @property
        def system_prompt(self) -> str:
            return {system_prompt_repr}

        @property
        def file_scope_patterns(self) -> list[str]:
            return {file_scope_repr}

        @property
        def is_writer(self) -> bool:
            return {is_writer}
''')


def _to_class_name(role_name: str) -> str:
    """Convert 'my_role_name' to 'MyRoleNamePlugin'."""
    return "".join(part.capitalize() for part in role_name.split("_")) + "Plugin"


@router.post(
    "",
    response_model=PluginInfo,
    status_code=201,
    summary="Create a new plugin",
    responses={
        400: {"description": "Invalid role name or plugin already exists"},
    },
)
async def create_plugin(body: CreatePluginRequest) -> PluginInfo:
    """Generate a plugin Python file and load it into the registry."""
    name = body.role_name.strip().lower()

    if not _ROLE_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role name '{name}'. Use lowercase letters, digits, and underscores (1-64 chars).",
        )

    if registry.get_metadata(name) is not None:
        raise HTTPException(status_code=400, detail=f"Plugin '{name}' already exists.")

    plugins_dir = registry._dir
    plugin_path = plugins_dir / f"{name}.py"
    if plugin_path.exists():
        raise HTTPException(status_code=400, detail=f"File '{plugin_path.name}' already exists.")

    source = _PLUGIN_TEMPLATE.format(
        role_name=name,
        class_name=_to_class_name(name),
        role_name_repr=repr(name),
        system_prompt_repr=repr(body.system_prompt),
        file_scope_repr=repr(body.file_scope_patterns),
        is_writer=body.is_writer,
    )

    plugins_dir.mkdir(parents=True, exist_ok=True)
    plugin_path.write_text(source)
    logger.info("POST /api/plugins — wrote plugin file %s", plugin_path.name)

    loaded_name = registry._load_file(plugin_path)
    if loaded_name is None:
        plugin_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Plugin file was generated but failed to load.")

    return _get_plugin_info(name)


@router.delete(
    "/{name}",
    status_code=200,
    summary="Delete a plugin",
    responses={404: {"description": "Plugin not found"}},
)
async def delete_plugin(name: str) -> dict[str, str]:
    """Delete the plugin file and unload it from the registry."""
    meta = registry.get_metadata(name)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

    plugin_path = meta.source_file
    registry._unload_file(plugin_path)

    if plugin_path.exists():
        plugin_path.unlink()
        logger.info("DELETE /api/plugins/%s — removed %s", name, plugin_path.name)

    return {"detail": f"Plugin '{name}' deleted."}


@router.post(
    "/{name}/enable",
    response_model=PluginInfo,
    summary="Enable a plugin",
    responses={404: {"description": "Plugin not found"}},
)
async def enable_plugin(name: str) -> PluginInfo:
    """Enable the named plugin so it participates in agent dispatch."""
    # Verify plugin exists first (get_metadata is lock-safe)
    meta = registry.get_metadata(name)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

    registry.enable(name)
    logger.info("POST /api/plugins/%s/enable — plugin enabled", name)
    return _get_plugin_info(name)


@router.post(
    "/{name}/disable",
    response_model=PluginInfo,
    summary="Disable a plugin",
    responses={404: {"description": "Plugin not found"}},
)
async def disable_plugin(name: str) -> PluginInfo:
    """Disable the named plugin so it is excluded from agent dispatch."""
    meta = registry.get_metadata(name)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

    registry.disable(name)
    logger.info("POST /api/plugins/%s/disable — plugin disabled", name)
    return _get_plugin_info(name)
