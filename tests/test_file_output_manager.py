"""Tests for file_output_manager — JIT Context artifact registry."""

from __future__ import annotations

import json
import os
from pathlib import Path

from contracts import (
    AgentRole,
    Artifact,
    ArtifactType,
    TaskInput,
    TaskOutput,
    TaskStatus,
)
from file_output_manager import ArtifactRegistry, infer_file_type

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_output(
    task_id: str,
    artifacts: list[str],
    status=TaskStatus.COMPLETED,
    structured: list[Artifact] | None = None,
) -> TaskOutput:
    return TaskOutput(
        task_id=task_id,
        status=status,
        summary="Done",
        artifacts=artifacts,
        structured_artifacts=structured or [],
    )


def _make_task(
    task_id: str, context_from: list[str] | None = None, input_artifacts: list[str] | None = None
) -> TaskInput:
    return TaskInput(
        id=task_id,
        role=AgentRole.BACKEND_DEVELOPER,
        goal="Implement the backend API endpoints for user authentication and session management",
        context_from=context_from or [],
        input_artifacts=input_artifacts or [],
    )


# ── infer_file_type ──────────────────────────────────────────────────────────


class TestInferFileType:
    def test_python(self):
        assert infer_file_type("src/main.py") == "code"

    def test_typescript(self):
        assert infer_file_type("app/page.tsx") == "code"

    def test_json(self):
        assert infer_file_type("package.json") == "data"

    def test_markdown(self):
        assert infer_file_type("README.md") == "doc"

    def test_image(self):
        assert infer_file_type("logo.png") == "asset"

    def test_unknown(self):
        assert infer_file_type("Makefile") == "file"

    def test_lockfile(self):
        assert infer_file_type("pnpm-lock.lock") == "lockfile"


# ── ArtifactRegistry ─────────────────────────────────────────────────────────


class TestArtifactRegistry:
    def test_register_plain_artifacts(self, tmp_path):
        """Register files from TaskOutput.artifacts list."""
        (tmp_path / "api.py").write_text("# api")
        (tmp_path / "models.py").write_text("# models")

        reg = ArtifactRegistry(str(tmp_path))
        output = _make_output("task_001", ["api.py", "models.py"])
        count = reg.register(output)

        assert count == 2
        assert len(reg._refs["task_001"]) == 2

    def test_register_structured_artifacts(self, tmp_path):
        """Register files from TaskOutput.structured_artifacts."""
        (tmp_path / "schema.json").write_text("{}")

        art = Artifact(
            type=ArtifactType.API_CONTRACT,
            title="User API Schema",
            file_path="schema.json",
        )
        output = _make_output("task_002", [], structured=[art])
        reg = ArtifactRegistry(str(tmp_path))
        count = reg.register(output)

        assert count == 1
        assert reg._refs["task_002"][0].description == "User API Schema"

    def test_skip_failed_output(self, tmp_path):
        """Do not register artifacts from failed tasks."""
        reg = ArtifactRegistry(str(tmp_path))
        output = _make_output("task_003", ["file.py"], status=TaskStatus.FAILED)
        count = reg.register(output)

        assert count == 0

    def test_skip_nonexistent_files(self, tmp_path):
        """Skip files that do not exist on disk."""
        reg = ArtifactRegistry(str(tmp_path))
        output = _make_output("task_004", ["ghost.py"])
        count = reg.register(output)

        assert count == 0

    def test_deduplicate_structured_and_plain(self, tmp_path):
        """Same path in both structured and plain should appear once."""
        (tmp_path / "shared.py").write_text("# shared")

        art = Artifact(
            type=ArtifactType.API_CONTRACT,
            title="Shared Module",
            file_path="shared.py",
        )
        output = _make_output("task_005", ["shared.py"], structured=[art])
        reg = ArtifactRegistry(str(tmp_path))
        count = reg.register(output)

        assert count == 1  # not 2

    def test_get_refs_for_downstream_task(self, tmp_path):
        """Downstream task gets refs from upstream context_from."""
        (tmp_path / "api.py").write_text("# api")

        reg = ArtifactRegistry(str(tmp_path))
        reg.register(_make_output("task_001", ["api.py"]))

        downstream = _make_task("task_002", context_from=["task_001"])
        refs = reg.get_refs_for_task(downstream)

        assert len(refs) == 1
        assert refs[0].path == "api.py"

    def test_enhance_prompt_adds_section(self, tmp_path):
        """enhance_prompt injects artifact references into the prompt."""
        (tmp_path / "api.py").write_text("# api")

        reg = ArtifactRegistry(str(tmp_path))
        reg.register(_make_output("task_001", ["api.py"]))

        downstream = _make_task("task_002", context_from=["task_001"])
        enhanced = reg.enhance_prompt(downstream, "Original prompt.")

        assert "Original prompt." in enhanced
        assert "api.py" in enhanced
        assert "source of truth" in enhanced

    def test_enhance_prompt_noop_when_no_refs(self, tmp_path):
        """enhance_prompt returns original prompt when no refs exist."""
        reg = ArtifactRegistry(str(tmp_path))
        task = _make_task("task_001")
        result = reg.enhance_prompt(task, "Original.")

        assert result == "Original."

    def test_input_artifacts_included(self, tmp_path):
        """input_artifacts on the task itself are included in refs."""
        (tmp_path / "spec.md").write_text("# spec")

        reg = ArtifactRegistry(str(tmp_path))
        task = _make_task("task_001", input_artifacts=["spec.md"])
        refs = reg.get_refs_for_task(task)

        assert len(refs) == 1
        assert refs[0].path == "spec.md"

    def test_manifest_saved(self, tmp_path):
        """save_manifest writes a valid JSON file."""
        (tmp_path / "app.py").write_text("# app")

        reg = ArtifactRegistry(str(tmp_path))
        reg.register(_make_output("task_001", ["app.py"]))

        manifest_path = reg.save_manifest()
        assert os.path.exists(manifest_path)

        with open(manifest_path) as f:
            data = json.load(f)
        assert "task_001" in data
        assert data["task_001"][0]["path"] == "app.py"

    def test_stats(self, tmp_path):
        """stats returns correct counts."""
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.json").write_text("")

        reg = ArtifactRegistry(str(tmp_path))
        reg.register(_make_output("t1", ["a.py"]))
        reg.register(_make_output("t2", ["b.json"]))

        s = reg.stats()
        assert s["total_tasks"] == 2
        assert s["total_artifacts"] == 2
        assert s["by_type"]["code"] == 1
        assert s["by_type"]["data"] == 1

    def test_resolve_absolute_path(self, tmp_path):
        """Absolute paths are used as-is."""
        abs_path = str(tmp_path / "abs.py")
        Path(abs_path).write_text("")

        reg = ArtifactRegistry(str(tmp_path))
        output = _make_output("t1", [abs_path])
        count = reg.register(output)

        assert count == 1
