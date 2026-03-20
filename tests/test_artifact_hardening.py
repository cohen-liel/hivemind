"""Tests for artifact hardening — ArtifactRegistry validation, partial registration, size limits.

Covers: file_output_manager.py (task_002)
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from contracts import ArtifactType, TaskInput, TaskOutput, TaskStatus, Artifact
from file_output_manager import (
    ARTIFACT_MAX_SIZE_BYTES,
    ArtifactRef,
    ArtifactRegistry,
    infer_file_type,
    _safe_file_size,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_task_input(
    task_id: str = "task_001",
    context_from: list[str] | None = None,
    input_artifacts: list[str] | None = None,
    expected_input_artifact_types: list[ArtifactType] | None = None,
) -> TaskInput:
    return TaskInput(
        id=task_id,
        role="backend_developer",
        goal="Implement a feature for testing purposes",
        context_from=context_from or [],
        input_artifacts=input_artifacts or [],
        expected_input_artifact_types=expected_input_artifact_types or [],
    )


def _make_task_output(
    task_id: str = "task_001",
    status: TaskStatus = TaskStatus.COMPLETED,
    artifacts: list[str] | None = None,
    structured_artifacts: list[Artifact] | None = None,
) -> TaskOutput:
    return TaskOutput(
        task_id=task_id,
        status=status,
        summary="Test output summary for artifact testing",
        artifacts=artifacts or [],
        structured_artifacts=structured_artifacts or [],
    )


# ── Tests: infer_file_type ──────────────────────────────────────────────────


class TestInferFileType:
    def test_infer_file_type_when_python_should_return_code(self):
        assert infer_file_type("main.py") == "code"

    def test_infer_file_type_when_typescript_should_return_code(self):
        assert infer_file_type("app.tsx") == "code"

    def test_infer_file_type_when_json_should_return_data(self):
        assert infer_file_type("config.json") == "data"

    def test_infer_file_type_when_markdown_should_return_doc(self):
        assert infer_file_type("README.md") == "doc"

    def test_infer_file_type_when_png_should_return_asset(self):
        assert infer_file_type("logo.png") == "asset"

    def test_infer_file_type_when_unknown_ext_should_return_file(self):
        assert infer_file_type("data.xyz") == "file"

    def test_infer_file_type_when_lock_should_return_lockfile(self):
        assert infer_file_type("package.lock") == "lockfile"


# ── Tests: ArtifactRegistry.register ────────────────────────────────────────


class TestArtifactRegistration:
    def test_register_when_completed_task_with_files_should_register_all(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        # Create real files
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("print('hello')")
        (tmp_path / "src" / "util.py").write_text("def helper(): pass")

        output = _make_task_output(
            artifacts=["src/app.py", "src/util.py"],
        )
        count = registry.register(output)
        assert count == 2

    def test_register_when_failed_task_without_allow_partial_should_skip(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "half.py").write_text("partial")

        output = _make_task_output(
            status=TaskStatus.FAILED,
            artifacts=["half.py"],
        )
        count = registry.register(output, allow_partial=False)
        assert count == 0

    def test_register_when_failed_task_with_allow_partial_should_register(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "half.py").write_text("partial work")

        output = _make_task_output(
            status=TaskStatus.FAILED,
            artifacts=["half.py"],
        )
        count = registry.register(output, allow_partial=True)
        assert count == 1
        refs = registry._refs["task_001"]
        assert refs[0].partial is True

    def test_register_when_file_missing_should_skip_gracefully(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        output = _make_task_output(
            artifacts=["nonexistent.py"],
        )
        count = registry.register(output)
        assert count == 0

    def test_register_when_structured_artifacts_should_use_title(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "schema.json").write_text('{"tables": []}')

        output = _make_task_output(
            structured_artifacts=[
                Artifact(
                    type=ArtifactType.SCHEMA,
                    title="Database Schema",
                    file_path="schema.json",
                    data={"tables": []},
                )
            ],
        )
        count = registry.register(output)
        assert count == 1
        ref = registry._refs["task_001"][0]
        assert ref.description == "Database Schema"
        assert ref.file_type == "data"

    def test_register_when_duplicate_paths_should_deduplicate(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "dup.py").write_text("code")

        output = _make_task_output(
            artifacts=["dup.py"],
            structured_artifacts=[
                Artifact(type=ArtifactType.CUSTOM, title="Dup File", file_path="dup.py")
            ],
        )
        count = registry.register(output)
        assert count == 1  # Deduplicated


# ── Tests: Artifact Size Limits ─────────────────────────────────────────────


class TestArtifactSizeLimits:
    def test_register_when_file_exceeds_limit_should_mark_truncated(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        big_file = tmp_path / "big.py"
        big_file.write_bytes(b"x" * (ARTIFACT_MAX_SIZE_BYTES + 1))

        output = _make_task_output(artifacts=["big.py"])
        count = registry.register(output)
        assert count == 1
        ref = registry._refs["task_001"][0]
        assert ref.truncated is True
        assert ref.size_bytes > ARTIFACT_MAX_SIZE_BYTES

    def test_register_when_file_under_limit_should_not_truncate(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        small_file = tmp_path / "small.py"
        small_file.write_text("print('hello')")

        output = _make_task_output(artifacts=["small.py"])
        registry.register(output)
        ref = registry._refs["task_001"][0]
        assert ref.truncated is False

    def test_register_when_exactly_at_limit_should_not_truncate(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        exact_file = tmp_path / "exact.py"
        exact_file.write_bytes(b"x" * ARTIFACT_MAX_SIZE_BYTES)

        output = _make_task_output(artifacts=["exact.py"])
        registry.register(output)
        ref = registry._refs["task_001"][0]
        assert ref.truncated is False


# ── Tests: Pre-execution Validation ─────────────────────────────────────────


class TestPreExecutionValidation:
    def test_validate_when_upstream_has_artifacts_should_return_empty(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "out.py").write_text("result")

        upstream_output = _make_task_output(
            task_id="task_upstream", artifacts=["out.py"]
        )
        registry.register(upstream_output)

        task = _make_task_input(task_id="task_002", context_from=["task_upstream"])
        warnings = registry.validate_pre_execution(task)
        assert warnings == []

    def test_validate_when_upstream_missing_should_warn(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        task = _make_task_input(task_id="task_002", context_from=["task_missing"])
        warnings = registry.validate_pre_execution(task)
        assert len(warnings) == 1
        assert "no artifacts were registered" in warnings[0]

    def test_validate_when_upstream_has_empty_artifacts_should_warn(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        # Register with no files existing -> 0 artifacts
        upstream_output = _make_task_output(task_id="task_upstream", artifacts=[])
        registry.register(upstream_output)

        task = _make_task_input(task_id="task_002", context_from=["task_upstream"])
        warnings = registry.validate_pre_execution(task)
        assert len(warnings) == 1
        assert "zero artifacts" in warnings[0]

    def test_validate_when_upstream_has_partial_artifacts_should_warn(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "partial.py").write_text("incomplete")

        upstream_output = _make_task_output(
            task_id="task_upstream", status=TaskStatus.FAILED, artifacts=["partial.py"]
        )
        registry.register(upstream_output, allow_partial=True)

        task = _make_task_input(task_id="task_002", context_from=["task_upstream"])
        warnings = registry.validate_pre_execution(task)
        assert len(warnings) == 1
        assert "partial" in warnings[0]

    def test_validate_when_expected_types_missing_should_warn(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        task = _make_task_input(
            task_id="task_002",
            context_from=["task_upstream"],
            expected_input_artifact_types=[ArtifactType.API_CONTRACT],
        )
        warnings = registry.validate_pre_execution(task)
        assert any("artifact type" in w.lower() or "no artifacts" in w.lower() for w in warnings)

    def test_validate_when_input_artifact_file_missing_should_warn(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        task = _make_task_input(
            task_id="task_002",
            input_artifacts=["nonexistent_file.json"],
        )
        warnings = registry.validate_pre_execution(task)
        assert len(warnings) == 1
        assert "does not exist" in warnings[0]

    def test_validate_when_input_artifact_file_exists_should_not_warn(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "existing.json").write_text("{}")
        task = _make_task_input(
            task_id="task_002",
            input_artifacts=["existing.json"],
        )
        warnings = registry.validate_pre_execution(task)
        assert warnings == []


# ── Tests: Prompt Enhancement ───────────────────────────────────────────────


class TestPromptEnhancement:
    def test_enhance_when_no_upstream_refs_should_return_unchanged(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        task = _make_task_input(task_id="task_002")
        prompt = "Original prompt"
        result = registry.enhance_prompt(task, prompt)
        assert result == "Original prompt"

    def test_enhance_when_upstream_refs_should_inject_xml(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "api.py").write_text("def endpoint(): ...")

        upstream = _make_task_output(task_id="task_upstream", artifacts=["api.py"])
        registry.register(upstream)

        task = _make_task_input(task_id="task_002", context_from=["task_upstream"])
        result = registry.enhance_prompt(task, "Build the frontend")
        assert "<upstream_artifacts>" in result
        assert "api.py" in result
        assert "</upstream_artifacts>" in result

    def test_enhance_when_truncated_artifact_should_add_warning(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        big_file = tmp_path / "big.py"
        big_file.write_bytes(b"x" * (ARTIFACT_MAX_SIZE_BYTES + 1))

        upstream = _make_task_output(task_id="task_upstream", artifacts=["big.py"])
        registry.register(upstream)

        task = _make_task_input(task_id="task_002", context_from=["task_upstream"])
        result = registry.enhance_prompt(task, "Use the file")
        assert "truncated='true'" in result
        assert "<warning>" in result

    def test_enhance_when_partial_artifact_should_add_warning(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "partial.py").write_text("half done")

        upstream = _make_task_output(
            task_id="task_upstream", status=TaskStatus.FAILED, artifacts=["partial.py"]
        )
        registry.register(upstream, allow_partial=True)

        task = _make_task_input(task_id="task_002", context_from=["task_upstream"])
        result = registry.enhance_prompt(task, "Continue the work")
        assert "partial='true'" in result
        assert "failed task" in result.lower()


# ── Tests: Stats and Manifest ───────────────────────────────────────────────


class TestStatsAndManifest:
    def test_stats_when_mixed_artifacts_should_count_correctly(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "a.py").write_text("code")
        (tmp_path / "b.json").write_text("{}")
        big_file = tmp_path / "c.py"
        big_file.write_bytes(b"x" * (ARTIFACT_MAX_SIZE_BYTES + 1))

        registry.register(_make_task_output(task_id="t1", artifacts=["a.py"]))
        registry.register(
            _make_task_output(task_id="t2", status=TaskStatus.FAILED, artifacts=["b.json"]),
            allow_partial=True,
        )
        registry.register(_make_task_output(task_id="t3", artifacts=["c.py"]))

        stats = registry.stats()
        assert stats["total_tasks"] == 3
        assert stats["total_artifacts"] == 3
        assert stats["partial_artifacts"] == 1
        assert stats["truncated_artifacts"] == 1

    def test_save_manifest_should_write_json(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "code.py").write_text("print(1)")
        registry.register(_make_task_output(artifacts=["code.py"]))

        manifest_path = registry.save_manifest()
        assert os.path.exists(manifest_path)
        with open(manifest_path) as f:
            data = json.load(f)
        assert "task_001" in data
        assert data["task_001"][0]["path"] == "code.py"


# ── Tests: get_refs_for_task ────────────────────────────────────────────────


class TestGetRefsForTask:
    def test_get_refs_when_upstream_registered_should_return_refs(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "api.py").write_text("routes")
        registry.register(_make_task_output(task_id="task_upstream", artifacts=["api.py"]))

        task = _make_task_input(task_id="task_002", context_from=["task_upstream"])
        refs = registry.get_refs_for_task(task)
        assert len(refs) == 1
        assert refs[0].path == "api.py"

    def test_get_refs_when_input_artifact_exists_should_include(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        (tmp_path / "schema.json").write_text("{}")
        task = _make_task_input(task_id="task_002", input_artifacts=["schema.json"])
        refs = registry.get_refs_for_task(task)
        assert len(refs) == 1
        assert refs[0].task_id == "input"

    def test_get_refs_when_input_artifact_missing_should_skip(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        task = _make_task_input(task_id="task_002", input_artifacts=["missing.json"])
        refs = registry.get_refs_for_task(task)
        assert len(refs) == 0


# ── Tests: Edge cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_safe_file_size_when_missing_should_return_zero(self):
        assert _safe_file_size("/nonexistent/path/file.py") == 0

    def test_register_when_empty_output_should_return_zero(self, tmp_path):
        registry = ArtifactRegistry(str(tmp_path))
        output = _make_task_output(artifacts=[], structured_artifacts=[])
        count = registry.register(output)
        assert count == 0
