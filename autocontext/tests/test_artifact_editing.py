"""Tests for AC-248: Artifact-editing scenario family with artifact-based evaluation.

Validates the ArtifactEditingInterface ABC, supporting data models
(Artifact, ArtifactDiff, ArtifactValidationResult, ArtifactEditingResult),
family/pipeline registration, and end-to-end artifact editing scenarios.
"""

from __future__ import annotations

from typing import Any

import pytest

from autocontext.scenarios.artifact_editing import (
    Artifact,
    ArtifactDiff,
    ArtifactEditingInterface,
    ArtifactEditingResult,
    ArtifactValidationResult,
)

# ---------------------------------------------------------------------------
# Data model construction
# ---------------------------------------------------------------------------


class TestArtifact:
    def test_construction(self) -> None:
        artifact = Artifact(
            path="config/app.yaml",
            content="key: value",
            content_type="yaml",
        )
        assert artifact.path == "config/app.yaml"
        assert artifact.content == "key: value"
        assert artifact.content_type == "yaml"
        assert artifact.metadata == {}

    def test_with_metadata(self) -> None:
        artifact = Artifact(
            path="schema.json",
            content='{"type": "object"}',
            content_type="json",
            metadata={"version": "2.0", "schema_id": "users"},
        )
        assert artifact.metadata["version"] == "2.0"

    def test_to_dict_from_dict(self) -> None:
        artifact = Artifact(
            path="main.py",
            content="print('hello')",
            content_type="python",
            metadata={"lines": 1},
        )
        data = artifact.to_dict()
        restored = Artifact.from_dict(data)
        assert restored.path == artifact.path
        assert restored.content == artifact.content
        assert restored.content_type == artifact.content_type
        assert restored.metadata == artifact.metadata


class TestArtifactDiff:
    def test_modify_operation(self) -> None:
        diff = ArtifactDiff(
            path="config.yaml",
            operation="modify",
            before="key: old",
            after="key: new",
        )
        assert diff.operation == "modify"
        assert diff.before == "key: old"
        assert diff.after == "key: new"

    def test_create_operation(self) -> None:
        diff = ArtifactDiff(
            path="new_file.txt",
            operation="create",
            before=None,
            after="new content",
        )
        assert diff.before is None
        assert diff.after == "new content"

    def test_delete_operation(self) -> None:
        diff = ArtifactDiff(
            path="old_file.txt",
            operation="delete",
            before="old content",
            after=None,
        )
        assert diff.before == "old content"
        assert diff.after is None

    def test_to_dict_from_dict(self) -> None:
        diff = ArtifactDiff(path="f.txt", operation="modify", before="a", after="b")
        data = diff.to_dict()
        restored = ArtifactDiff.from_dict(data)
        assert restored.path == diff.path
        assert restored.operation == diff.operation
        assert restored.before == diff.before
        assert restored.after == diff.after


class TestArtifactValidationResult:
    def test_valid(self) -> None:
        result = ArtifactValidationResult(valid=True, errors=[], warnings=[])
        assert result.valid is True
        assert result.errors == []

    def test_invalid(self) -> None:
        result = ArtifactValidationResult(
            valid=False,
            errors=["missing required key 'name'"],
            warnings=["deprecated field 'legacy_id'"],
        )
        assert result.valid is False
        assert len(result.errors) == 1
        assert len(result.warnings) == 1


class TestArtifactEditingResult:
    def test_construction(self) -> None:
        result = ArtifactEditingResult(
            score=0.85,
            reasoning="Correctly modified config, minor precision issue",
            dimension_scores={"correctness": 0.95, "precision": 0.75},
            diffs=[ArtifactDiff(path="f.txt", operation="modify", before="a", after="b")],
            validation=ArtifactValidationResult(valid=True, errors=[], warnings=[]),
            artifacts_modified=1,
            artifacts_valid=1,
        )
        assert result.score == 0.85
        assert result.artifacts_modified == 1
        assert len(result.diffs) == 1

    def test_to_dict_from_dict(self) -> None:
        result = ArtifactEditingResult(
            score=0.7,
            reasoning="Partial",
            dimension_scores={"correctness": 0.6},
            diffs=[
                ArtifactDiff(path="a.txt", operation="modify", before="x", after="y"),
                ArtifactDiff(path="b.txt", operation="create", before=None, after="new"),
            ],
            validation=ArtifactValidationResult(valid=False, errors=["bad"], warnings=[]),
            artifacts_modified=2,
            artifacts_valid=1,
        )
        data = result.to_dict()
        restored = ArtifactEditingResult.from_dict(data)
        assert restored.score == result.score
        assert restored.reasoning == result.reasoning
        assert len(restored.diffs) == 2
        assert restored.validation.valid is False
        assert restored.artifacts_modified == 2


# ---------------------------------------------------------------------------
# ArtifactEditingInterface ABC
# ---------------------------------------------------------------------------


class _MockArtifactEditor(ArtifactEditingInterface):
    """Concrete test implementation for artifact editing."""

    name = "mock_editor"

    def describe_task(self) -> str:
        return "Fix the YAML config by adding a missing 'database' section"

    def get_rubric(self) -> str:
        return "Evaluate: correctness of YAML structure, completeness of required fields, minimal changes"

    def initial_artifacts(self, seed: int | None = None) -> list[Artifact]:
        return [
            Artifact(
                path="config/app.yaml",
                content="app:\n  name: myapp\n  port: 8080\n",
                content_type="yaml",
            ),
        ]

    def get_edit_prompt(self, artifacts: list[Artifact]) -> str:
        paths = ", ".join(a.path for a in artifacts)
        return f"Add a 'database' section with host, port, and name fields to: {paths}"

    def validate_artifact(self, artifact: Artifact) -> ArtifactValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        if artifact.content_type == "yaml":
            if "database:" not in artifact.content:
                errors.append("missing 'database' section")
            if "host:" not in artifact.content:
                errors.append("missing 'host' field in database section")
        return ArtifactValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    def evaluate_edits(
        self,
        original: list[Artifact],
        edited: list[Artifact],
    ) -> ArtifactEditingResult:
        diffs = self.compute_diffs(original, edited)

        all_valid = True
        total_errors: list[str] = []
        for artifact in edited:
            vr = self.validate_artifact(artifact)
            if not vr.valid:
                all_valid = False
                total_errors.extend(vr.errors)

        correctness = 1.0 if all_valid else max(0.0, 1.0 - len(total_errors) * 0.3)
        precision = 1.0 if len(diffs) <= 1 else max(0.0, 1.0 - (len(diffs) - 1) * 0.1)
        score = correctness * 0.7 + precision * 0.3

        return ArtifactEditingResult(
            score=score,
            reasoning=f"{'All' if all_valid else 'Not all'} artifacts valid, {len(diffs)} changes made",
            dimension_scores={"correctness": correctness, "precision": precision},
            diffs=diffs,
            validation=ArtifactValidationResult(valid=all_valid, errors=total_errors, warnings=[]),
            artifacts_modified=len(diffs),
            artifacts_valid=sum(1 for a in edited if self.validate_artifact(a).valid),
        )

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        artifacts = self.initial_artifacts(seed)
        return {"artifacts": [a.to_dict() for a in artifacts], "seed": seed or 0}


class TestArtifactEditingInterfaceABC:
    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            ArtifactEditingInterface()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        editor = _MockArtifactEditor()
        assert editor.name == "mock_editor"

    def test_describe_task(self) -> None:
        editor = _MockArtifactEditor()
        assert "YAML" in editor.describe_task()

    def test_get_rubric(self) -> None:
        editor = _MockArtifactEditor()
        rubric = editor.get_rubric()
        assert "correctness" in rubric

    def test_initial_artifacts(self) -> None:
        editor = _MockArtifactEditor()
        artifacts = editor.initial_artifacts()
        assert len(artifacts) == 1
        assert artifacts[0].path == "config/app.yaml"
        assert artifacts[0].content_type == "yaml"

    def test_get_edit_prompt(self) -> None:
        editor = _MockArtifactEditor()
        artifacts = editor.initial_artifacts()
        prompt = editor.get_edit_prompt(artifacts)
        assert "database" in prompt
        assert "config/app.yaml" in prompt

    def test_validate_artifact_invalid(self) -> None:
        editor = _MockArtifactEditor()
        artifact = Artifact(path="config.yaml", content="app:\n  name: test\n", content_type="yaml")
        result = editor.validate_artifact(artifact)
        assert result.valid is False
        assert any("database" in e for e in result.errors)

    def test_validate_artifact_valid(self) -> None:
        editor = _MockArtifactEditor()
        artifact = Artifact(
            path="config.yaml",
            content="app:\n  name: test\ndatabase:\n  host: localhost\n  port: 5432\n",
            content_type="yaml",
        )
        result = editor.validate_artifact(artifact)
        assert result.valid is True

    def test_initial_state(self) -> None:
        editor = _MockArtifactEditor()
        state = editor.initial_state(seed=42)
        assert "artifacts" in state
        assert isinstance(state["artifacts"], list)


# ---------------------------------------------------------------------------
# Default compute_diffs
# ---------------------------------------------------------------------------


class TestComputeDiffs:
    def test_modification_detected(self) -> None:
        editor = _MockArtifactEditor()
        original = [Artifact(path="f.txt", content="old", content_type="text")]
        edited = [Artifact(path="f.txt", content="new", content_type="text")]
        diffs = editor.compute_diffs(original, edited)
        assert len(diffs) == 1
        assert diffs[0].operation == "modify"
        assert diffs[0].before == "old"
        assert diffs[0].after == "new"

    def test_no_change_produces_no_diff(self) -> None:
        editor = _MockArtifactEditor()
        original = [Artifact(path="f.txt", content="same", content_type="text")]
        edited = [Artifact(path="f.txt", content="same", content_type="text")]
        diffs = editor.compute_diffs(original, edited)
        assert diffs == []

    def test_create_detected(self) -> None:
        editor = _MockArtifactEditor()
        original = [Artifact(path="a.txt", content="a", content_type="text")]
        edited = [
            Artifact(path="a.txt", content="a", content_type="text"),
            Artifact(path="b.txt", content="b", content_type="text"),
        ]
        diffs = editor.compute_diffs(original, edited)
        creates = [d for d in diffs if d.operation == "create"]
        assert len(creates) == 1
        assert creates[0].path == "b.txt"

    def test_delete_detected(self) -> None:
        editor = _MockArtifactEditor()
        original = [
            Artifact(path="a.txt", content="a", content_type="text"),
            Artifact(path="b.txt", content="b", content_type="text"),
        ]
        edited = [Artifact(path="a.txt", content="a", content_type="text")]
        diffs = editor.compute_diffs(original, edited)
        deletes = [d for d in diffs if d.operation == "delete"]
        assert len(deletes) == 1
        assert deletes[0].path == "b.txt"


# ---------------------------------------------------------------------------
# End-to-end evaluation
# ---------------------------------------------------------------------------


class TestEndToEndEvaluation:
    def test_correct_edit(self) -> None:
        editor = _MockArtifactEditor()
        original = editor.initial_artifacts()
        edited = [
            Artifact(
                path="config/app.yaml",
                content="app:\n  name: myapp\n  port: 8080\ndatabase:\n  host: localhost\n  port: 5432\n  name: mydb\n",
                content_type="yaml",
            ),
        ]
        result = editor.evaluate_edits(original, edited)
        assert result.score > 0.8
        assert result.validation.valid is True
        assert result.artifacts_valid == 1
        assert result.dimension_scores["correctness"] == 1.0

    def test_wrong_edit(self) -> None:
        """Edit that doesn't add the required database section."""
        editor = _MockArtifactEditor()
        original = editor.initial_artifacts()
        edited = [
            Artifact(
                path="config/app.yaml",
                content="app:\n  name: myapp\n  port: 9090\n",
                content_type="yaml",
            ),
        ]
        result = editor.evaluate_edits(original, edited)
        assert result.validation.valid is False
        assert result.score < 0.8
        assert result.dimension_scores["correctness"] < 1.0

    def test_invalid_artifact_state(self) -> None:
        """Edit produces structurally invalid artifact."""
        editor = _MockArtifactEditor()
        original = editor.initial_artifacts()
        edited = [
            Artifact(
                path="config/app.yaml",
                content="",  # Empty config
                content_type="yaml",
            ),
        ]
        result = editor.evaluate_edits(original, edited)
        assert result.validation.valid is False
        assert result.artifacts_valid == 0


# ---------------------------------------------------------------------------
# Family registry integration
# ---------------------------------------------------------------------------


class TestFamilyRegistration:
    def test_artifact_editing_family_registered(self) -> None:
        from autocontext.scenarios.families import get_family

        family = get_family("artifact_editing")
        assert family.name == "artifact_editing"
        assert family.evaluation_mode == "artifact_validation"

    def test_artifact_editing_scenario_type_marker(self) -> None:
        from autocontext.scenarios.families import get_family

        family = get_family("artifact_editing")
        assert family.scenario_type_marker == "artifact_editing"

    def test_detect_family_for_instance(self) -> None:
        from autocontext.scenarios.families import detect_family

        editor = _MockArtifactEditor()
        family = detect_family(editor)
        assert family is not None
        assert family.name == "artifact_editing"


# ---------------------------------------------------------------------------
# Pipeline registry integration
# ---------------------------------------------------------------------------


class TestPipelineRegistration:
    def test_pipeline_registered(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import has_pipeline

        assert has_pipeline("artifact_editing") is True

    def test_pipeline_spec_validation_valid(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {
            "task_description": "Fix the config file",
            "artifacts": [
                {"path": "config.yaml", "content": "key: val", "content_type": "yaml"},
            ],
            "validation_rules": ["must contain 'database' section"],
            "rubric": "Evaluate correctness and precision",
        }
        errors = validate_for_family("artifact_editing", spec)
        assert errors == []

    def test_pipeline_spec_validation_missing_fields(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {"task_description": "Fix something"}
        errors = validate_for_family("artifact_editing", spec)
        assert len(errors) > 0
        assert any("artifacts" in e for e in errors)

    def test_pipeline_source_validation(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = '''
from autocontext.scenarios.artifact_editing import ArtifactEditingInterface

class MyEditor(ArtifactEditingInterface):
    name = "my_editor"
    def describe_task(self): return "task"
    def get_rubric(self): return "rubric"
    def initial_artifacts(self, seed=None): return []
    def get_edit_prompt(self, artifacts): return "edit"
    def validate_artifact(self, artifact): pass
    def evaluate_edits(self, original, edited): pass
'''
        errors = validate_source_for_family("artifact_editing", source)
        assert errors == []

    def test_pipeline_source_wrong_base_class(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = '''
class NotAnEditor:
    pass
'''
        errors = validate_source_for_family("artifact_editing", source)
        assert any("ArtifactEditingInterface" in e for e in errors)
