"""Tests for OpenClaw artifact contract schemas (MTS-194).

RED phase: these tests define the expected interface for HarnessArtifact,
PolicyArtifact, DistilledModelArtifact, and ArtifactManifest.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from autocontext.artifacts import (
    ArtifactManifest,
    ArtifactProvenance,
    DistilledModelArtifact,
    HarnessArtifact,
    PolicyArtifact,
)

# ---------------------------------------------------------------------------
# ArtifactProvenance
# ---------------------------------------------------------------------------


class TestArtifactProvenance:
    def test_basic_provenance(self) -> None:
        p = ArtifactProvenance(
            run_id="run_abc",
            generation=3,
            scenario="grid_ctf",
        )
        assert p.run_id == "run_abc"
        assert p.generation == 3
        assert p.scenario == "grid_ctf"
        assert p.settings == {}

    def test_provenance_with_settings(self) -> None:
        p = ArtifactProvenance(
            run_id="run_xyz",
            generation=1,
            scenario="othello",
            settings={"model": "claude-sonnet", "matches": 10},
        )
        assert p.settings["model"] == "claude-sonnet"

    def test_provenance_rejects_empty_run_id(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactProvenance(run_id="", generation=1, scenario="grid_ctf")

    def test_provenance_rejects_negative_generation(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactProvenance(run_id="run_1", generation=-1, scenario="grid_ctf")


# ---------------------------------------------------------------------------
# HarnessArtifact
# ---------------------------------------------------------------------------


class TestHarnessArtifact:
    def test_minimal_harness(self) -> None:
        h = HarnessArtifact(
            name="grid_ctf_validator",
            version=1,
            scenario="grid_ctf",
            source_code="def validate(s): return True",
            provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
        )
        assert h.artifact_type == "harness"
        assert h.name == "grid_ctf_validator"
        assert h.version == 1
        assert h.scenario == "grid_ctf"
        assert h.source_code == "def validate(s): return True"
        assert h.accuracy is None
        assert h.synthesis_iterations is None
        assert h.id is not None
        assert h.created_at is not None

    def test_harness_with_all_fields(self) -> None:
        h = HarnessArtifact(
            name="othello_harness",
            version=2,
            scenario="othello",
            source_code="def check(): pass",
            accuracy=0.95,
            synthesis_iterations=5,
            provenance=ArtifactProvenance(run_id="run_2", generation=3, scenario="othello"),
            compatible_scenarios=["othello", "chess"],
            tags=["validation", "board-games"],
        )
        assert h.accuracy == 0.95
        assert h.synthesis_iterations == 5
        assert h.compatible_scenarios == ["othello", "chess"]
        assert h.tags == ["validation", "board-games"]

    def test_harness_json_roundtrip(self) -> None:
        h = HarnessArtifact(
            name="test_harness",
            version=1,
            scenario="grid_ctf",
            source_code="def test(): pass",
            provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
        )
        json_str = h.model_dump_json()
        h2 = HarnessArtifact.model_validate_json(json_str)
        assert h2.name == h.name
        assert h2.version == h.version
        assert h2.source_code == h.source_code
        assert h2.provenance.run_id == h.provenance.run_id
        assert h2.id == h.id
        assert h2.artifact_type == "harness"

    def test_harness_rejects_empty_source(self) -> None:
        with pytest.raises(ValidationError):
            HarnessArtifact(
                name="bad",
                version=1,
                scenario="grid_ctf",
                source_code="",
                provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
            )

    def test_harness_rejects_zero_version(self) -> None:
        with pytest.raises(ValidationError):
            HarnessArtifact(
                name="bad",
                version=0,
                scenario="grid_ctf",
                source_code="pass",
                provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
            )

    def test_harness_accuracy_range(self) -> None:
        with pytest.raises(ValidationError):
            HarnessArtifact(
                name="bad",
                version=1,
                scenario="grid_ctf",
                source_code="pass",
                accuracy=1.5,
                provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
            )

    def test_harness_rejects_mismatched_artifact_type(self) -> None:
        with pytest.raises(ValidationError):
            HarnessArtifact(
                name="bad",
                version=1,
                scenario="grid_ctf",
                artifact_type="policy",
                source_code="pass",
                provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
            )


# ---------------------------------------------------------------------------
# PolicyArtifact
# ---------------------------------------------------------------------------


class TestPolicyArtifact:
    def test_minimal_policy(self) -> None:
        p = PolicyArtifact(
            name="aggressive_ctf",
            version=1,
            scenario="grid_ctf",
            source_code="def policy(state): return {'aggression': 0.9}",
            provenance=ArtifactProvenance(run_id="run_1", generation=5, scenario="grid_ctf"),
        )
        assert p.artifact_type == "policy"
        assert p.name == "aggressive_ctf"
        assert p.heuristic_value is None
        assert p.match_results == []

    def test_policy_with_match_results(self) -> None:
        p = PolicyArtifact(
            name="balanced_play",
            version=3,
            scenario="othello",
            source_code="def policy(s): return {}",
            heuristic_value=0.78,
            match_results=[
                {"opponent": "random", "wins": 8, "losses": 2},
                {"opponent": "greedy", "wins": 5, "losses": 5},
            ],
            provenance=ArtifactProvenance(run_id="run_3", generation=10, scenario="othello"),
        )
        assert p.heuristic_value == 0.78
        assert len(p.match_results) == 2

    def test_policy_json_roundtrip(self) -> None:
        p = PolicyArtifact(
            name="test_policy",
            version=1,
            scenario="grid_ctf",
            source_code="def policy(s): return {}",
            heuristic_value=0.6,
            provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
        )
        json_str = p.model_dump_json()
        p2 = PolicyArtifact.model_validate_json(json_str)
        assert p2.name == p.name
        assert p2.heuristic_value == p.heuristic_value
        assert p2.artifact_type == "policy"

    def test_policy_rejects_empty_source(self) -> None:
        with pytest.raises(ValidationError):
            PolicyArtifact(
                name="bad",
                version=1,
                scenario="grid_ctf",
                source_code="",
                provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
            )

    def test_policy_rejects_mismatched_artifact_type(self) -> None:
        with pytest.raises(ValidationError):
            PolicyArtifact(
                name="bad",
                version=1,
                scenario="grid_ctf",
                artifact_type="harness",
                source_code="pass",
                provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
            )


# ---------------------------------------------------------------------------
# DistilledModelArtifact
# ---------------------------------------------------------------------------


class TestDistilledModelArtifact:
    def test_minimal_model(self) -> None:
        m = DistilledModelArtifact(
            name="ctf_local_v1",
            version=1,
            scenario="grid_ctf",
            architecture="transformer",
            parameter_count=1_000_000,
            checkpoint_path="/models/ctf_v1.pt",
            provenance=ArtifactProvenance(run_id="run_1", generation=10, scenario="grid_ctf"),
        )
        assert m.artifact_type == "distilled_model"
        assert m.name == "ctf_local_v1"
        assert m.architecture == "transformer"
        assert m.parameter_count == 1_000_000
        assert m.checkpoint_path == "/models/ctf_v1.pt"
        assert m.training_data_stats == {}

    def test_model_with_training_stats(self) -> None:
        m = DistilledModelArtifact(
            name="othello_v2",
            version=2,
            scenario="othello",
            architecture="mlp",
            parameter_count=500_000,
            checkpoint_path="/models/othello_v2.pt",
            training_data_stats={"samples": 10000, "epochs": 50, "loss": 0.02},
            provenance=ArtifactProvenance(run_id="run_2", generation=20, scenario="othello"),
        )
        assert m.training_data_stats["samples"] == 10000

    def test_model_json_roundtrip(self) -> None:
        m = DistilledModelArtifact(
            name="test_model",
            version=1,
            scenario="grid_ctf",
            architecture="cnn",
            parameter_count=100_000,
            checkpoint_path="/tmp/model.pt",
            provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
        )
        json_str = m.model_dump_json()
        m2 = DistilledModelArtifact.model_validate_json(json_str)
        assert m2.name == m.name
        assert m2.architecture == m.architecture
        assert m2.parameter_count == m.parameter_count
        assert m2.artifact_type == "distilled_model"

    def test_model_rejects_zero_params(self) -> None:
        with pytest.raises(ValidationError):
            DistilledModelArtifact(
                name="bad",
                version=1,
                scenario="grid_ctf",
                architecture="transformer",
                parameter_count=0,
                checkpoint_path="/tmp/model.pt",
                provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
            )

    def test_model_rejects_empty_checkpoint(self) -> None:
        with pytest.raises(ValidationError):
            DistilledModelArtifact(
                name="bad",
                version=1,
                scenario="grid_ctf",
                architecture="transformer",
                parameter_count=100,
                checkpoint_path="",
                provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
            )

    def test_model_rejects_mismatched_artifact_type(self) -> None:
        with pytest.raises(ValidationError):
            DistilledModelArtifact(
                name="bad",
                version=1,
                scenario="grid_ctf",
                artifact_type="policy",
                architecture="transformer",
                parameter_count=100,
                checkpoint_path="/tmp/model.pt",
                provenance=ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf"),
            )


# ---------------------------------------------------------------------------
# ArtifactManifest
# ---------------------------------------------------------------------------


class TestArtifactManifest:
    def test_empty_manifest(self) -> None:
        m = ArtifactManifest()
        assert m.harnesses == []
        assert m.policies == []
        assert m.distilled_models == []
        assert m.created_at is not None

    def test_manifest_with_artifacts(self) -> None:
        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        h = HarnessArtifact(name="h1", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov)
        p = PolicyArtifact(name="p1", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov)
        manifest = ArtifactManifest(harnesses=[h], policies=[p])
        assert len(manifest.harnesses) == 1
        assert len(manifest.policies) == 1
        assert len(manifest.distilled_models) == 0

    def test_manifest_json_roundtrip(self) -> None:
        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        h = HarnessArtifact(name="h1", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov)
        p = PolicyArtifact(name="p1", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov)
        d = DistilledModelArtifact(
            name="m1", version=1, scenario="grid_ctf", architecture="mlp",
            parameter_count=100, checkpoint_path="/tmp/m.pt", provenance=prov,
        )
        manifest = ArtifactManifest(harnesses=[h], policies=[p], distilled_models=[d])
        json_str = manifest.model_dump_json()
        m2 = ArtifactManifest.model_validate_json(json_str)
        assert len(m2.harnesses) == 1
        assert len(m2.policies) == 1
        assert len(m2.distilled_models) == 1
        assert m2.harnesses[0].name == "h1"
        assert m2.policies[0].name == "p1"
        assert m2.distilled_models[0].name == "m1"

    def test_manifest_to_dict(self) -> None:
        """Verify we can produce a plain dict for serialization."""
        manifest = ArtifactManifest()
        d = manifest.model_dump()
        assert isinstance(d, dict)
        assert "harnesses" in d
        assert "policies" in d
        assert "distilled_models" in d

    def test_manifest_all_artifacts_property(self) -> None:
        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        h = HarnessArtifact(name="h1", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov)
        p = PolicyArtifact(name="p1", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov)
        manifest = ArtifactManifest(harnesses=[h], policies=[p])
        all_arts = manifest.all_artifacts()
        assert len(all_arts) == 2


# ---------------------------------------------------------------------------
# Cross-type tests
# ---------------------------------------------------------------------------


class TestCrossTypeValidation:
    """Verify that all artifact types share a common structure."""

    def test_all_have_id_version_created_at(self) -> None:
        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        h = HarnessArtifact(name="h", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov)
        p = PolicyArtifact(name="p", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov)
        d = DistilledModelArtifact(
            name="d", version=1, scenario="grid_ctf", architecture="mlp",
            parameter_count=100, checkpoint_path="/tmp/m.pt", provenance=prov,
        )
        for art in [h, p, d]:
            assert art.id is not None
            assert len(art.id) > 0
            assert art.version >= 1
            assert art.created_at is not None
            assert art.provenance.run_id == "run_1"

    def test_json_dict_roundtrip_all_types(self) -> None:
        """Verify JSON dict serialization works for all types."""
        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        artifacts = [
            HarnessArtifact(name="h", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov),
            PolicyArtifact(name="p", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov),
            DistilledModelArtifact(
                name="d", version=1, scenario="grid_ctf", architecture="mlp",
                parameter_count=100, checkpoint_path="/tmp/m.pt", provenance=prov,
            ),
        ]
        for art in artifacts:
            d = json.loads(art.model_dump_json())
            assert d["artifact_type"] in ("harness", "policy", "distilled_model")
            assert d["name"] == art.name
            assert d["version"] == art.version
