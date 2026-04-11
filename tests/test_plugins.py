"""Comprehensive tests for the Hivemind plugin system.

Covers:
- PluginRegistry discovery (using tmp_path for isolation)
- Enable / disable state transitions
- Invalid plugin detection (missing required fields)
- Hot-reload: modifying a temp plugin file updates the registry
- API endpoint tests via FastAPI TestClient
- contracts.py role validation (plugin roles accepted, unknown roles rejected)
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers — create minimal valid / invalid plugin source code
# ---------------------------------------------------------------------------

_VALID_PLUGIN_SRC = textwrap.dedent(
    """\
    from plugin_registry import PluginBase

    class MyTestPlugin(PluginBase):
        @property
        def role_name(self) -> str:
            return "my_test_role"

        @property
        def system_prompt(self) -> str:
            return "You are a test agent with a very long prompt so preview works correctly."

        @property
        def file_scope_patterns(self) -> list:
            return ["**/*.py"]

        @property
        def is_writer(self) -> bool:
            return False
    """
)

_VALID_PLUGIN_WRITER_SRC = textwrap.dedent(
    """\
    from plugin_registry import PluginBase

    class WriterTestPlugin(PluginBase):
        @property
        def role_name(self) -> str:
            return "writer_test_role"

        @property
        def system_prompt(self) -> str:
            return "You are a writer agent that produces documentation files."

        @property
        def file_scope_patterns(self) -> list:
            return ["docs/**/*.md", "**/*.ts"]

        @property
        def is_writer(self) -> bool:
            return True
    """
)

# Missing role_name — abstract property not implemented → will fail with TypeError
_INVALID_PLUGIN_SRC = textwrap.dedent(
    """\
    from plugin_registry import PluginBase

    class BrokenPlugin(PluginBase):
        # Missing role_name, system_prompt, file_scope_patterns, is_writer
        pass
    """
)

# File with no PluginBase subclass at all
_NO_CLASS_PLUGIN_SRC = textwrap.dedent(
    """\
    def some_function():
        return 42
    """
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def plugin_dir(tmp_path: Path) -> Path:
    """Return an empty temp directory to use as plugins_dir."""
    d = tmp_path / "plugins"
    d.mkdir()
    return d


@pytest.fixture()
def registry_with_dir(plugin_dir: Path):
    """Return a fresh PluginRegistry pointing at *plugin_dir*."""
    # Import here so conftest path setup takes effect first
    from plugin_registry import PluginRegistry

    return PluginRegistry(plugins_dir=plugin_dir)


@pytest.fixture()
def registry_with_valid_plugin(plugin_dir: Path, registry_with_dir):
    """Load one valid plugin into the registry, return (registry, plugin_path)."""
    plugin_path = plugin_dir / "my_test_plugin.py"
    plugin_path.write_text(_VALID_PLUGIN_SRC)
    roles = registry_with_dir.discover()
    assert "my_test_role" in roles, f"Expected 'my_test_role' in {roles}"
    return registry_with_dir, plugin_path


# ---------------------------------------------------------------------------
# 1. Discovery tests
# ---------------------------------------------------------------------------


class TestPluginDiscovery:
    def test_discover_empty_dir_when_no_plugins_returns_empty_list(self, registry_with_dir):
        roles = registry_with_dir.discover()
        assert roles == []

    def test_discover_valid_plugin_file_loads_correctly(self, plugin_dir, registry_with_dir):
        plugin_path = plugin_dir / "my_test_plugin.py"
        plugin_path.write_text(_VALID_PLUGIN_SRC)

        roles = registry_with_dir.discover()

        assert "my_test_role" in roles

    def test_discover_multiple_plugins_loads_all(self, plugin_dir, registry_with_dir):
        (plugin_dir / "plugin_a.py").write_text(_VALID_PLUGIN_SRC)
        (plugin_dir / "plugin_b.py").write_text(_VALID_PLUGIN_WRITER_SRC)

        roles = registry_with_dir.discover()

        assert "my_test_role" in roles
        assert "writer_test_role" in roles
        assert len(roles) == 2

    def test_discover_skips_dunder_files(self, plugin_dir, registry_with_dir):
        (plugin_dir / "__init__.py").write_text("# package init")
        (plugin_dir / "_private_helper.py").write_text("x = 1")

        roles = registry_with_dir.discover()

        assert roles == []

    def test_discover_plugin_metadata_is_correct(self, plugin_dir, registry_with_dir):
        (plugin_dir / "my_test_plugin.py").write_text(_VALID_PLUGIN_SRC)
        registry_with_dir.discover()

        meta = registry_with_dir.get_metadata("my_test_role")

        assert meta is not None
        assert meta.role_name == "my_test_role"
        assert meta.enabled is True
        assert meta.instance is not None
        assert meta.instance.is_writer is False
        assert meta.instance.file_scope_patterns == ["**/*.py"]

    def test_discover_writer_plugin_metadata_is_correct(self, plugin_dir, registry_with_dir):
        (plugin_dir / "writer_plugin.py").write_text(_VALID_PLUGIN_WRITER_SRC)
        registry_with_dir.discover()

        meta = registry_with_dir.get_metadata("writer_test_role")

        assert meta is not None
        assert meta.instance.is_writer is True
        assert "docs/**/*.md" in meta.instance.file_scope_patterns

    def test_list_all_returns_serialisable_dicts(self, plugin_dir, registry_with_dir):
        (plugin_dir / "my_test_plugin.py").write_text(_VALID_PLUGIN_SRC)
        registry_with_dir.discover()

        items = registry_with_dir.list_all()

        assert len(items) == 1
        item = items[0]
        assert "role_name" in item
        assert "enabled" in item
        assert "is_writer" in item
        assert "file_scope_patterns" in item
        assert "system_prompt_preview" in item

    def test_get_returns_instance_for_enabled_plugin(self, registry_with_valid_plugin):
        reg, _ = registry_with_valid_plugin
        instance = reg.get("my_test_role")
        assert instance is not None
        assert instance.role_name == "my_test_role"

    def test_role_names_returns_all_registered_names(self, plugin_dir, registry_with_dir):
        (plugin_dir / "plugin_a.py").write_text(_VALID_PLUGIN_SRC)
        (plugin_dir / "plugin_b.py").write_text(_VALID_PLUGIN_WRITER_SRC)
        registry_with_dir.discover()

        names = registry_with_dir.role_names()

        assert set(names) == {"my_test_role", "writer_test_role"}


# ---------------------------------------------------------------------------
# 2. Enable / Disable state transition tests
# ---------------------------------------------------------------------------


class TestEnableDisable:
    def test_newly_discovered_plugin_is_enabled_by_default(self, registry_with_valid_plugin):
        reg, _ = registry_with_valid_plugin
        meta = reg.get_metadata("my_test_role")
        assert meta.enabled is True

    def test_disable_plugin_when_exists_returns_true(self, registry_with_valid_plugin):
        reg, _ = registry_with_valid_plugin
        result = reg.disable("my_test_role")
        assert result is True

    def test_disable_plugin_when_does_not_exist_returns_false(self, registry_with_valid_plugin):
        reg, _ = registry_with_valid_plugin
        result = reg.disable("nonexistent_role")
        assert result is False

    def test_disable_plugin_makes_it_inactive(self, registry_with_valid_plugin):
        reg, _ = registry_with_valid_plugin
        reg.disable("my_test_role")

        meta = reg.get_metadata("my_test_role")
        assert meta.enabled is False

    def test_get_returns_none_for_disabled_plugin(self, registry_with_valid_plugin):
        reg, _ = registry_with_valid_plugin
        reg.disable("my_test_role")

        instance = reg.get("my_test_role")
        assert instance is None

    def test_enable_disabled_plugin_returns_true(self, registry_with_valid_plugin):
        reg, _ = registry_with_valid_plugin
        reg.disable("my_test_role")
        result = reg.enable("my_test_role")
        assert result is True

    def test_enable_plugin_makes_it_active_again(self, registry_with_valid_plugin):
        reg, _ = registry_with_valid_plugin
        reg.disable("my_test_role")
        reg.enable("my_test_role")

        meta = reg.get_metadata("my_test_role")
        assert meta.enabled is True
        instance = reg.get("my_test_role")
        assert instance is not None

    def test_enable_nonexistent_plugin_returns_false(self, registry_with_valid_plugin):
        reg, _ = registry_with_valid_plugin
        result = reg.enable("nonexistent_role")
        assert result is False

    def test_list_enabled_excludes_disabled_plugins(self, plugin_dir, registry_with_dir):
        (plugin_dir / "plugin_a.py").write_text(_VALID_PLUGIN_SRC)
        (plugin_dir / "plugin_b.py").write_text(_VALID_PLUGIN_WRITER_SRC)
        registry_with_dir.discover()

        registry_with_dir.disable("my_test_role")

        enabled = registry_with_dir.list_enabled()
        enabled_names = [p.role_name for p in enabled]
        assert "writer_test_role" in enabled_names
        assert "my_test_role" not in enabled_names

    def test_disable_then_enable_preserves_plugin_data(self, registry_with_valid_plugin):
        reg, _ = registry_with_valid_plugin
        reg.disable("my_test_role")
        reg.enable("my_test_role")

        instance = reg.get("my_test_role")
        assert instance is not None
        assert instance.role_name == "my_test_role"
        assert instance.file_scope_patterns == ["**/*.py"]


# ---------------------------------------------------------------------------
# 3. Invalid plugin tests
# ---------------------------------------------------------------------------


class TestInvalidPlugin:
    def test_abstract_plugin_class_not_loaded(self, plugin_dir, registry_with_dir):
        """A class that doesn't implement abstract methods cannot be instantiated."""
        (plugin_dir / "broken_plugin.py").write_text(_INVALID_PLUGIN_SRC)
        roles = registry_with_dir.discover()
        assert "BrokenPlugin" not in roles
        assert len(roles) == 0

    def test_file_with_no_plugin_class_not_loaded(self, plugin_dir, registry_with_dir):
        (plugin_dir / "no_class.py").write_text(_NO_CLASS_PLUGIN_SRC)
        roles = registry_with_dir.discover()
        assert roles == []

    def test_invalid_plugin_does_not_affect_valid_plugins(self, plugin_dir, registry_with_dir):
        """Registry still loads valid plugins even if one file is invalid."""
        (plugin_dir / "broken_plugin.py").write_text(_INVALID_PLUGIN_SRC)
        (plugin_dir / "valid_plugin.py").write_text(_VALID_PLUGIN_SRC)

        roles = registry_with_dir.discover()

        assert "my_test_role" in roles
        assert len(roles) == 1

    def test_syntax_error_in_plugin_file_does_not_crash_registry(
        self, plugin_dir, registry_with_dir
    ):
        (plugin_dir / "syntax_error_plugin.py").write_text("this is not valid python !!!@@@")
        roles = registry_with_dir.discover()
        assert roles == []

    def test_get_metadata_for_missing_plugin_returns_none(self, registry_with_dir):
        meta = registry_with_dir.get_metadata("does_not_exist")
        assert meta is None

    def test_get_for_missing_plugin_returns_none(self, registry_with_dir):
        result = registry_with_dir.get("does_not_exist")
        assert result is None


# ---------------------------------------------------------------------------
# 4. Hot-reload tests (direct _load_file calls, no watchfiles dependency)
# ---------------------------------------------------------------------------


class TestHotReload:
    def test_load_file_directly_adds_plugin_to_registry(self, plugin_dir, registry_with_dir):
        plugin_path = plugin_dir / "hot_test.py"
        plugin_path.write_text(_VALID_PLUGIN_SRC)

        result = registry_with_dir._load_file(plugin_path)

        assert result == "my_test_role"
        assert registry_with_dir.get("my_test_role") is not None

    def test_reload_file_after_modification_updates_registry(self, plugin_dir, registry_with_dir):
        """Simulate hot-reload: write file, load it, modify role_name, reload."""
        plugin_path = plugin_dir / "hot_test.py"
        plugin_path.write_text(_VALID_PLUGIN_SRC)
        registry_with_dir._load_file(plugin_path)

        assert registry_with_dir.get("my_test_role") is not None

        # Now update the plugin source to use a different role name
        updated_src = _VALID_PLUGIN_SRC.replace('return "my_test_role"', 'return "my_updated_role"')
        plugin_path.write_text(updated_src)
        registry_with_dir._load_file(plugin_path)

        # The new role should be registered
        assert registry_with_dir.get("my_updated_role") is not None

    def test_reload_preserves_disabled_state_across_reload(self, plugin_dir, registry_with_dir):
        """If a plugin was disabled before reload, it stays disabled after reload."""
        plugin_path = plugin_dir / "hot_test.py"
        plugin_path.write_text(_VALID_PLUGIN_SRC)
        registry_with_dir._load_file(plugin_path)

        # Disable the plugin
        registry_with_dir.disable("my_test_role")
        assert registry_with_dir.get_metadata("my_test_role").enabled is False

        # Reload the same file (same role_name)
        plugin_path.write_text(_VALID_PLUGIN_SRC)
        registry_with_dir._load_file(plugin_path)

        # Should still be disabled
        meta = registry_with_dir.get_metadata("my_test_role")
        assert meta is not None
        assert meta.enabled is False

    def test_unload_file_removes_plugin_from_registry(self, plugin_dir, registry_with_dir):
        plugin_path = plugin_dir / "hot_test.py"
        plugin_path.write_text(_VALID_PLUGIN_SRC)
        registry_with_dir._load_file(plugin_path)
        assert registry_with_dir.get("my_test_role") is not None

        registry_with_dir._unload_file(plugin_path)

        assert registry_with_dir.get_metadata("my_test_role") is None
        assert registry_with_dir.get("my_test_role") is None

    def test_unload_nonexistent_file_does_not_raise(self, plugin_dir, registry_with_dir):
        """Unloading a path that was never loaded should be a no-op."""
        registry_with_dir._unload_file(plugin_dir / "does_not_exist.py")  # should not raise

    def test_load_file_returns_none_for_invalid_plugin(self, plugin_dir, registry_with_dir):
        plugin_path = plugin_dir / "broken.py"
        plugin_path.write_text(_INVALID_PLUGIN_SRC)

        result = registry_with_dir._load_file(plugin_path)

        assert result is None


# ---------------------------------------------------------------------------
# 5. API endpoint tests via FastAPI TestClient
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_registry(plugin_dir: Path):
    """A fresh PluginRegistry with two plugins pre-loaded, patched into api.plugins."""
    from plugin_registry import PluginRegistry

    reg = PluginRegistry(plugins_dir=plugin_dir)
    (plugin_dir / "plugin_a.py").write_text(_VALID_PLUGIN_SRC)
    (plugin_dir / "plugin_b.py").write_text(_VALID_PLUGIN_WRITER_SRC)
    reg.discover()
    return reg


@pytest.fixture()
def api_client(api_registry):
    """TestClient for the plugins router, using a freshly patched registry."""
    from fastapi import FastAPI

    # Import the router module and patch its `registry` symbol
    import api.plugins as plugins_module

    app = FastAPI()

    # Patch the module-level registry used by the router
    with patch.object(plugins_module, "registry", api_registry):
        # Re-include the router so the patched registry is live
        app.include_router(plugins_module.router)
        with TestClient(app) as client:
            yield client, api_registry


class TestPluginsAPIListEndpoint:
    def test_get_plugins_returns_200(self, api_client):
        client, _ = api_client
        response = client.get("/api/plugins")
        assert response.status_code == 200

    def test_get_plugins_returns_plugin_list(self, api_client):
        client, _ = api_client
        response = client.get("/api/plugins")
        data = response.json()
        assert "plugins" in data
        assert isinstance(data["plugins"], list)

    def test_get_plugins_returns_both_plugins(self, api_client):
        client, _ = api_client
        response = client.get("/api/plugins")
        plugins = response.json()["plugins"]
        names = {p["name"] for p in plugins}
        assert "my_test_role" in names
        assert "writer_test_role" in names

    def test_get_plugins_response_has_correct_fields(self, api_client):
        client, _ = api_client
        response = client.get("/api/plugins")
        plugin = response.json()["plugins"][0]
        assert "name" in plugin
        assert "description" in plugin
        assert "is_writer" in plugin
        assert "file_scope_patterns" in plugin
        assert "enabled" in plugin

    def test_get_plugins_shows_enabled_state(self, api_client):
        client, reg = api_client
        # Disable one plugin before the request
        reg.disable("my_test_role")
        response = client.get("/api/plugins")
        plugins = {p["name"]: p for p in response.json()["plugins"]}
        assert plugins["my_test_role"]["enabled"] is False
        assert plugins["writer_test_role"]["enabled"] is True


class TestPluginsAPIEnableEndpoint:
    def test_enable_known_plugin_returns_200(self, api_client):
        client, reg = api_client
        reg.disable("my_test_role")
        response = client.post("/api/plugins/my_test_role/enable")
        assert response.status_code == 200

    def test_enable_known_plugin_returns_plugin_info(self, api_client):
        client, reg = api_client
        reg.disable("my_test_role")
        response = client.post("/api/plugins/my_test_role/enable")
        data = response.json()
        assert data["name"] == "my_test_role"
        assert data["enabled"] is True

    def test_enable_unknown_plugin_returns_404(self, api_client):
        client, _ = api_client
        response = client.post("/api/plugins/nonexistent_plugin/enable")
        assert response.status_code == 404

    def test_enable_unknown_plugin_returns_detail_message(self, api_client):
        client, _ = api_client
        response = client.post("/api/plugins/nonexistent_plugin/enable")
        detail = response.json()["detail"]
        assert "nonexistent_plugin" in detail

    def test_enable_already_enabled_plugin_returns_200(self, api_client):
        """Enabling an already-enabled plugin should be idempotent."""
        client, _ = api_client
        response = client.post("/api/plugins/my_test_role/enable")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True


class TestPluginsAPIDisableEndpoint:
    def test_disable_known_plugin_returns_200(self, api_client):
        client, _ = api_client
        response = client.post("/api/plugins/my_test_role/disable")
        assert response.status_code == 200

    def test_disable_known_plugin_returns_plugin_info_with_enabled_false(self, api_client):
        client, _ = api_client
        response = client.post("/api/plugins/my_test_role/disable")
        data = response.json()
        assert data["name"] == "my_test_role"
        assert data["enabled"] is False

    def test_disable_unknown_plugin_returns_404(self, api_client):
        client, _ = api_client
        response = client.post("/api/plugins/ghost_plugin/disable")
        assert response.status_code == 404

    def test_disable_then_enable_via_api_round_trip(self, api_client):
        """Disable via API, verify disabled, re-enable via API, verify enabled."""
        client, _ = api_client
        client.post("/api/plugins/my_test_role/disable")
        mid_state = client.get("/api/plugins").json()["plugins"]
        mid_map = {p["name"]: p for p in mid_state}
        assert mid_map["my_test_role"]["enabled"] is False

        client.post("/api/plugins/my_test_role/enable")
        final_state = client.get("/api/plugins").json()["plugins"]
        final_map = {p["name"]: p for p in final_state}
        assert final_map["my_test_role"]["enabled"] is True

    def test_disable_writer_plugin_sets_is_writer_in_response(self, api_client):
        client, _ = api_client
        response = client.post("/api/plugins/writer_test_role/disable")
        data = response.json()
        assert data["is_writer"] is True
        assert data["enabled"] is False


class TestPluginsAPICreateEndpoint:
    def test_create_plugin_returns_201(self, api_client):
        client, _ = api_client
        response = client.post(
            "/api/plugins",
            json={
                "role_name": "new_custom_role",
                "system_prompt": "You are a custom agent that does interesting things.",
                "file_scope_patterns": ["**/*.py"],
                "is_writer": False,
            },
        )
        assert response.status_code == 201

    def test_create_plugin_returns_plugin_info(self, api_client):
        client, _ = api_client
        response = client.post(
            "/api/plugins",
            json={
                "role_name": "created_role",
                "system_prompt": "You are an agent created via the API endpoint.",
                "file_scope_patterns": ["docs/**/*.md"],
                "is_writer": True,
            },
        )
        data = response.json()
        assert data["name"] == "created_role"
        assert data["is_writer"] is True
        assert data["enabled"] is True

    def test_create_plugin_with_invalid_name_returns_400(self, api_client):
        client, _ = api_client
        response = client.post(
            "/api/plugins",
            json={
                "role_name": "INVALID NAME!!",
                "system_prompt": "Some prompt that is long enough for validation.",
            },
        )
        assert response.status_code == 400

    def test_create_plugin_with_existing_name_returns_400(self, api_client):
        client, _ = api_client
        response = client.post(
            "/api/plugins",
            json={
                "role_name": "my_test_role",
                "system_prompt": "This role already exists in the test registry.",
            },
        )
        assert response.status_code == 400

    def test_create_plugin_with_short_prompt_returns_422(self, api_client):
        client, _ = api_client
        response = client.post(
            "/api/plugins",
            json={
                "role_name": "short_prompt_role",
                "system_prompt": "Too short",
            },
        )
        assert response.status_code == 422


class TestPluginsAPIDeleteEndpoint:
    def test_delete_known_plugin_returns_200(self, api_client):
        client, reg = api_client
        response = client.delete("/api/plugins/my_test_role")
        assert response.status_code == 200

    def test_delete_known_plugin_removes_from_list(self, api_client):
        client, reg = api_client
        client.delete("/api/plugins/my_test_role")
        plugins = client.get("/api/plugins").json()["plugins"]
        names = {p["name"] for p in plugins}
        assert "my_test_role" not in names

    def test_delete_unknown_plugin_returns_404(self, api_client):
        client, _ = api_client
        response = client.delete("/api/plugins/nonexistent_role")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 6. contracts.py TaskInput role validation
# ---------------------------------------------------------------------------


class TestTaskInputRoleValidation:
    """Test that TaskInput.validate_role accepts/rejects roles correctly."""

    def _make_task_input(self, role: str, **kwargs) -> Any:
        """Helper: create a minimal TaskInput with the given role."""
        from contracts import TaskInput

        return TaskInput(
            id="task_001",
            role=role,
            goal="A sufficiently long goal description for the validator",
            **kwargs,
        )

    def test_builtin_role_backend_developer_is_accepted(self):
        task = self._make_task_input("backend_developer")
        assert task.role == "backend_developer"

    def test_builtin_role_frontend_developer_is_accepted(self):
        task = self._make_task_input("frontend_developer")
        assert task.role == "frontend_developer"

    def test_builtin_role_test_engineer_is_accepted(self):
        task = self._make_task_input("test_engineer")
        assert task.role == "test_engineer"

    def test_agent_role_enum_value_coerced_to_string(self):
        from contracts import AgentRole, TaskInput

        task = TaskInput(
            id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="A sufficiently long goal description for the validator",
        )
        assert task.role == "backend_developer"
        assert isinstance(task.role, str)

    def test_unknown_role_raises_validation_error(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            self._make_task_input("completely_unknown_role_xyz")
        assert (
            "unknown_role_xyz" in str(exc_info.value).lower()
            or "unknown" in str(exc_info.value).lower()
        )

    def test_empty_string_role_raises_validation_error(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._make_task_input("")

    def test_plugin_role_accepted_when_plugin_is_enabled(self, plugin_dir, registry_with_dir):
        """A plugin role name passes validation when its plugin is enabled."""
        (plugin_dir / "my_test_plugin.py").write_text(_VALID_PLUGIN_SRC)
        registry_with_dir.discover()

        with patch("plugin_registry.registry", registry_with_dir):
            # Also patch what contracts imports internally
            import plugin_registry

            original_registry = plugin_registry.registry
            plugin_registry.registry = registry_with_dir
            try:
                task = self._make_task_input("my_test_role")
                assert task.role == "my_test_role"
            finally:
                plugin_registry.registry = original_registry

    def test_plugin_role_rejected_when_plugin_is_disabled(self, plugin_dir, registry_with_dir):
        """A plugin role name fails validation when its plugin is disabled."""
        from pydantic import ValidationError

        (plugin_dir / "my_test_plugin.py").write_text(_VALID_PLUGIN_SRC)
        registry_with_dir.discover()
        registry_with_dir.disable("my_test_role")

        import plugin_registry

        original_registry = plugin_registry.registry
        plugin_registry.registry = registry_with_dir
        try:
            with pytest.raises(ValidationError):
                self._make_task_input("my_test_role")
        finally:
            plugin_registry.registry = original_registry

    def test_role_strips_whitespace(self):
        """Role validator should strip whitespace before checking."""
        # This tests that v.strip() is called before validation
        from contracts import TaskInput

        # AgentRole values are all lowercase, no spaces — check that
        # a valid role passes regardless (the strip behavior is tested indirectly)
        task = TaskInput(
            id="task_001",
            role="  backend_developer  ",
            goal="A sufficiently long goal description for the validator",
        )
        assert task.role == "backend_developer"

    def test_non_string_role_raises_validation_error(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._make_task_input(12345)  # type: ignore[arg-type]

    def test_all_builtin_agent_roles_are_valid(self):
        """Every AgentRole enum value should pass TaskInput validation."""
        from contracts import AgentRole, TaskInput

        for agent_role in AgentRole:
            task = TaskInput(
                id="task_001",
                role=agent_role.value,
                goal="A sufficiently long goal description for the validator",
            )
            assert task.role == agent_role.value


# ---------------------------------------------------------------------------
# 7. PluginBase contract / build_prompt tests
# ---------------------------------------------------------------------------


class TestPluginBase:
    def test_plugin_base_build_prompt_returns_system_prompt_by_default(
        self, plugin_dir, registry_with_dir
    ):
        (plugin_dir / "my_test_plugin.py").write_text(_VALID_PLUGIN_SRC)
        registry_with_dir.discover()
        instance = registry_with_dir.get("my_test_role")
        assert instance is not None
        result = instance.build_prompt()
        assert result == instance.system_prompt

    def test_plugin_base_build_prompt_with_context_returns_system_prompt(
        self, plugin_dir, registry_with_dir
    ):
        """Base class build_prompt ignores context (unless overridden)."""
        (plugin_dir / "my_test_plugin.py").write_text(_VALID_PLUGIN_SRC)
        registry_with_dir.discover()
        instance = registry_with_dir.get("my_test_role")
        result = instance.build_prompt(context={"key": "value"})
        assert instance.system_prompt in result

    def test_documentation_writer_plugin_build_prompt_injects_files(self, tmp_path):
        """DocumentationWriterPlugin overrides build_prompt to inject modified_files."""
        from plugins.documentation_writer import DocumentationWriterPlugin

        plugin = DocumentationWriterPlugin()
        context = {"modified_files": ["src/foo.py", "src/bar.ts"]}
        result = plugin.build_prompt(context=context)

        assert "src/foo.py" in result
        assert "src/bar.ts" in result
        assert plugin.system_prompt in result

    def test_documentation_writer_build_prompt_no_context_returns_base(self):
        from plugins.documentation_writer import DocumentationWriterPlugin

        plugin = DocumentationWriterPlugin()
        result = plugin.build_prompt()
        assert result == plugin.system_prompt

    def test_documentation_writer_plugin_properties(self):
        from plugins.documentation_writer import DocumentationWriterPlugin

        plugin = DocumentationWriterPlugin()
        assert plugin.role_name == "documentation_writer"
        assert plugin.is_writer is True
        assert "**/*.py" in plugin.file_scope_patterns
        assert len(plugin.system_prompt) > 50
