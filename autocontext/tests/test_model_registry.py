"""Tests for AC-287 + AC-288: distilled model registry and training publication.

AC-287: DistilledModelRecord, ModelRegistry, resolve_model
AC-288: DistilledModelArtifact, publish_training_output, TrainingCompletionOutput
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(**overrides: Any) -> Any:
    from autocontext.training.model_registry import DistilledModelRecord

    defaults: dict[str, Any] = {
        "artifact_id": "art-1",
        "scenario": "grid_ctf",
        "scenario_family": "game",
        "backend": "mlx",
        "checkpoint_path": "/models/grid_ctf/checkpoint-100",
        "runtime_types": ["provider"],
        "activation_state": "candidate",
        "training_metrics": {"loss": 0.42, "epochs": 10},
        "provenance": {"run_id": "train-1", "created_at": "2026-03-16T12:00:00Z"},
    }
    defaults.update(overrides)
    return DistilledModelRecord(**defaults)


# ===========================================================================
# AC-287: DistilledModelRecord
# ===========================================================================


class TestDistilledModelRecord:
    def test_construction(self) -> None:
        rec = _record()
        assert rec.artifact_id == "art-1"
        assert rec.activation_state == "candidate"

    def test_roundtrip(self) -> None:
        from autocontext.training.model_registry import DistilledModelRecord

        rec = _record(activation_state="active", backend="cuda")
        d = rec.to_dict()
        restored = DistilledModelRecord.from_dict(d)
        assert restored.backend == "cuda"
        assert restored.activation_state == "active"

    def test_valid_activation_states(self) -> None:
        for state in ("candidate", "active", "disabled", "deprecated"):
            rec = _record(activation_state=state)
            assert rec.activation_state == state


# ===========================================================================
# AC-287: ModelRegistry
# ===========================================================================


class TestModelRegistry:
    def test_register_and_load(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import ModelRegistry

        registry = ModelRegistry(tmp_path)
        rec = _record()
        registry.register(rec)

        loaded = registry.load("art-1")
        assert loaded is not None
        assert loaded.scenario == "grid_ctf"

    def test_load_missing(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import ModelRegistry

        registry = ModelRegistry(tmp_path)
        assert registry.load("nonexistent") is None

    def test_list_for_scenario(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import ModelRegistry

        registry = ModelRegistry(tmp_path)
        registry.register(_record(artifact_id="a1", scenario="grid_ctf"))
        registry.register(_record(artifact_id="a2", scenario="grid_ctf"))
        registry.register(_record(artifact_id="a3", scenario="othello"))

        grid_models = registry.list_for_scenario("grid_ctf")
        assert len(grid_models) == 2

    def test_activate(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import ModelRegistry

        registry = ModelRegistry(tmp_path)
        registry.register(_record(artifact_id="a1", activation_state="candidate"))
        registry.activate("a1")

        loaded = registry.load("a1")
        assert loaded is not None
        assert loaded.activation_state == "active"

    def test_activate_deactivates_previous(self, tmp_path: Path) -> None:
        """Only one model should be active per scenario+backend+runtime slot."""
        from autocontext.training.model_registry import ModelRegistry

        registry = ModelRegistry(tmp_path)
        registry.register(_record(artifact_id="a1", scenario="grid_ctf", backend="mlx", activation_state="active"))
        registry.register(_record(artifact_id="a2", scenario="grid_ctf", backend="mlx", activation_state="candidate"))

        registry.activate("a2")

        a1 = registry.load("a1")
        a2 = registry.load("a2")
        assert a1 is not None and a1.activation_state != "active"
        assert a2 is not None and a2.activation_state == "active"

    def test_activate_keeps_distinct_runtime_slot_active(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import ModelRegistry

        registry = ModelRegistry(tmp_path)
        registry.register(
            _record(
                artifact_id="provider-1",
                scenario="grid_ctf",
                backend="mlx",
                runtime_types=["provider"],
                activation_state="active",
            )
        )
        registry.register(
            _record(
                artifact_id="judge-1",
                scenario="grid_ctf",
                backend="mlx",
                runtime_types=["judge"],
                activation_state="active",
            )
        )
        registry.register(
            _record(
                artifact_id="provider-2",
                scenario="grid_ctf",
                backend="mlx",
                runtime_types=["provider"],
                activation_state="candidate",
            )
        )

        registry.activate("provider-2")

        provider_1 = registry.load("provider-1")
        judge_1 = registry.load("judge-1")
        provider_2 = registry.load("provider-2")
        assert provider_1 is not None and provider_1.activation_state == "disabled"
        assert judge_1 is not None and judge_1.activation_state == "active"
        assert provider_2 is not None and provider_2.activation_state == "active"

    def test_deactivate(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import ModelRegistry

        registry = ModelRegistry(tmp_path)
        registry.register(_record(artifact_id="a1", activation_state="active"))
        registry.deactivate("a1")

        loaded = registry.load("a1")
        assert loaded is not None
        assert loaded.activation_state == "disabled"


# ===========================================================================
# AC-287: resolve_model
# ===========================================================================


class TestResolveModel:
    def test_returns_active_model(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import ModelRegistry, resolve_model

        registry = ModelRegistry(tmp_path)
        registry.register(_record(artifact_id="a1", scenario="grid_ctf", backend="mlx", activation_state="active"))

        result = resolve_model(registry, scenario="grid_ctf", backend="mlx")
        assert result is not None
        assert result.artifact_id == "a1"

    def test_manual_override_takes_precedence(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import ModelRegistry, resolve_model

        registry = ModelRegistry(tmp_path)
        registry.register(_record(artifact_id="a1", scenario="grid_ctf", activation_state="active"))

        result = resolve_model(
            registry, scenario="grid_ctf", backend="mlx",
            manual_override="a-override",
        )
        assert result is not None
        assert result.artifact_id == "a-override"

    def test_returns_none_when_no_active(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import ModelRegistry, resolve_model

        registry = ModelRegistry(tmp_path)
        registry.register(_record(artifact_id="a1", scenario="grid_ctf", activation_state="candidate"))

        result = resolve_model(registry, scenario="grid_ctf", backend="mlx")
        assert result is None

    def test_filters_by_backend(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import ModelRegistry, resolve_model

        registry = ModelRegistry(tmp_path)
        registry.register(_record(artifact_id="mlx-1", scenario="grid_ctf", backend="mlx", activation_state="active"))
        registry.register(_record(artifact_id="cuda-1", scenario="grid_ctf", backend="cuda", activation_state="active"))

        result = resolve_model(registry, scenario="grid_ctf", backend="cuda")
        assert result is not None
        assert result.artifact_id == "cuda-1"


# ===========================================================================
# AC-288: DistilledModelArtifact
# ===========================================================================


class TestDistilledModelArtifact:
    def test_construction(self) -> None:
        from autocontext.training.model_registry import DistilledModelArtifact

        art = DistilledModelArtifact(
            artifact_id="art-pub-1",
            checkpoint_path="/models/grid_ctf/final",
            backend="mlx",
            scenario="grid_ctf",
            parameter_count=125_000_000,
            architecture="llama-3b-lora",
            training_metrics={"loss": 0.35, "epochs": 20},
            data_stats={"samples": 5000, "scenario_gens": 50},
        )
        assert art.parameter_count == 125_000_000

    def test_roundtrip(self) -> None:
        from autocontext.training.model_registry import DistilledModelArtifact

        art = DistilledModelArtifact(
            artifact_id="art-pub-2",
            checkpoint_path="/models/test",
            backend="cuda",
            scenario="othello",
            parameter_count=0,
            architecture="",
            training_metrics={},
            data_stats={},
        )
        d = art.to_dict()
        restored = DistilledModelArtifact.from_dict(d)
        assert restored.backend == "cuda"


# ===========================================================================
# AC-288: publish_training_output
# ===========================================================================


class TestPublishTrainingOutput:
    def test_publishes_and_registers(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import (
            ModelRegistry,
            TrainingCompletionOutput,
            publish_training_output,
        )

        registry = ModelRegistry(tmp_path)
        completion = TrainingCompletionOutput(
            run_id="train-42",
            checkpoint_path="/models/grid_ctf/ckpt-final",
            backend="mlx",
            scenario="grid_ctf",
            scenario_family="game",
            parameter_count=125_000_000,
            architecture="llama-3b-lora",
            training_metrics={"loss": 0.3},
            data_stats={"samples": 10000},
        )

        record = publish_training_output(completion, registry)
        assert record.activation_state == "candidate"
        assert record.scenario == "grid_ctf"

        # Should be in registry
        loaded = registry.load(record.artifact_id)
        assert loaded is not None

    def test_idempotent_republish(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import (
            ModelRegistry,
            TrainingCompletionOutput,
            publish_training_output,
        )

        registry = ModelRegistry(tmp_path)
        completion = TrainingCompletionOutput(
            run_id="train-42",
            checkpoint_path="/models/grid_ctf/ckpt",
            backend="mlx",
            scenario="grid_ctf",
        )

        r1 = publish_training_output(completion, registry)
        r2 = publish_training_output(completion, registry)
        assert r1.artifact_id == r2.artifact_id

    def test_auto_activate_when_requested(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import (
            ModelRegistry,
            TrainingCompletionOutput,
            publish_training_output,
        )

        registry = ModelRegistry(tmp_path)
        completion = TrainingCompletionOutput(
            run_id="train-42",
            checkpoint_path="/models/grid_ctf/ckpt",
            backend="mlx",
            scenario="grid_ctf",
        )

        record = publish_training_output(completion, registry, auto_activate=True)
        assert record.activation_state == "active"

    def test_persists_openclaw_artifact_when_root_provided(self, tmp_path: Path) -> None:
        from autocontext.training.model_registry import (
            ModelRegistry,
            TrainingCompletionOutput,
            publish_training_output,
        )

        registry = ModelRegistry(tmp_path)
        completion = TrainingCompletionOutput(
            run_id="train-99",
            checkpoint_path="/models/grid_ctf/ckpt",
            backend="mlx",
            scenario="grid_ctf",
            scenario_family="game",
            parameter_count=125_000_000,
            architecture="autoresearch_gpt",
            training_metrics={"loss": 0.2},
            data_stats={"samples": 2048},
        )

        record = publish_training_output(
            completion,
            registry,
            artifacts_root=tmp_path,
            auto_activate=True,
        )

        artifact_path = tmp_path / "_openclaw_artifacts" / f"{record.artifact_id}.json"
        assert artifact_path.exists()
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert payload["artifact_type"] == "distilled_model"
