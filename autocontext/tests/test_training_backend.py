"""Tests for AC-286: training backend abstraction and end-to-end activation flow.

Covers: TrainingBackend ABC, MLXBackend, CUDABackend, BackendRegistry,
end_to_end_activation_flow.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

# ===========================================================================
# TrainingBackend ABC
# ===========================================================================


class TestTrainingBackend:
    def test_mlx_backend_name(self) -> None:
        from autocontext.training.backends import MLXBackend

        backend = MLXBackend()
        assert backend.name == "mlx"

    def test_cuda_backend_name(self) -> None:
        from autocontext.training.backends import CUDABackend

        backend = CUDABackend()
        assert backend.name == "cuda"

    def test_mlx_supported(self) -> None:
        from autocontext.training.backends import MLXBackend

        backend = MLXBackend()
        # MLX is supported on darwin (macOS), may not be on other platforms
        # Just verify the method exists and returns a bool
        assert isinstance(backend.is_available(), bool)

    def test_cuda_not_available_without_gpu(self) -> None:
        from autocontext.training.backends import CUDABackend

        backend = CUDABackend()
        # In test environment, CUDA is typically not available
        assert isinstance(backend.is_available(), bool)

    def test_cuda_requires_actual_cuda_runtime(self, monkeypatch) -> None:
        from autocontext.training.backends import CUDABackend

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: False),
        )
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        with patch("importlib.util.find_spec", return_value=object()):
            assert CUDABackend().is_available() is False

    def test_cuda_available_when_torch_reports_cuda(self, monkeypatch) -> None:
        from autocontext.training.backends import CUDABackend

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: True),
        )
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        with patch("importlib.util.find_spec", return_value=object()):
            assert CUDABackend().is_available() is True

    def test_mlx_default_checkpoint_dir(self) -> None:
        from autocontext.training.backends import MLXBackend

        backend = MLXBackend()
        path = backend.default_checkpoint_dir("grid_ctf")
        assert "grid_ctf" in str(path)
        assert "mlx" in str(path)

    def test_cuda_default_checkpoint_dir(self) -> None:
        from autocontext.training.backends import CUDABackend

        backend = CUDABackend()
        path = backend.default_checkpoint_dir("othello")
        assert "othello" in str(path)
        assert "cuda" in str(path)

    def test_cuda_publishes_checkpoint_artifacts_only(self) -> None:
        from autocontext.training.backends import CUDABackend

        assert CUDABackend().supported_runtime_types() == ["checkpoint"]

    def test_backend_metadata(self) -> None:
        from autocontext.training.backends import MLXBackend

        backend = MLXBackend()
        meta = backend.metadata()
        assert meta["name"] == "mlx"
        assert "runtime_types" in meta


# ===========================================================================
# BackendRegistry
# ===========================================================================


class TestBackendRegistry:
    def test_register_and_get(self) -> None:
        from autocontext.training.backends import BackendRegistry, MLXBackend

        registry = BackendRegistry()
        registry.register(MLXBackend())

        backend = registry.get("mlx")
        assert backend is not None
        assert backend.name == "mlx"

    def test_get_unknown_returns_none(self) -> None:
        from autocontext.training.backends import BackendRegistry

        registry = BackendRegistry()
        assert registry.get("unknown") is None

    def test_list_backends(self) -> None:
        from autocontext.training.backends import (
            BackendRegistry,
            CUDABackend,
            MLXBackend,
        )

        registry = BackendRegistry()
        registry.register(MLXBackend())
        registry.register(CUDABackend())

        names = registry.list_names()
        assert "mlx" in names
        assert "cuda" in names

    def test_default_registry_has_builtins(self) -> None:
        from autocontext.training.backends import default_backend_registry

        registry = default_backend_registry()
        assert registry.get("mlx") is not None
        assert registry.get("cuda") is not None


# ===========================================================================
# End-to-end activation flow
# ===========================================================================


class TestEndToEndActivationFlow:
    def test_publish_activate_resolve(self, tmp_path: Path) -> None:
        """Full chain: training completion → publish → activate → resolve."""
        from autocontext.providers.scenario_routing import (
            ScenarioRoutingContext,
            resolve_provider_for_context,
        )
        from autocontext.training.backends import MLXBackend
        from autocontext.training.model_registry import (
            ModelRegistry,
            TrainingCompletionOutput,
            publish_training_output,
        )

        registry = ModelRegistry(tmp_path)
        backend = MLXBackend()

        # 1. Training completes
        completion = TrainingCompletionOutput(
            run_id="train-e2e",
            checkpoint_path="/models/grid_ctf/e2e-checkpoint",
            backend=backend.name,
            scenario="grid_ctf",
            scenario_family="game",
            parameter_count=125_000_000,
            architecture="llama-3b-lora",
            training_metrics={"loss": 0.3},
        )

        # 2. Publish and auto-activate
        record = publish_training_output(completion, registry, auto_activate=True)
        assert record.activation_state == "active"

        # 3. Resolve via routing
        ctx = ScenarioRoutingContext(
            scenario="grid_ctf",
            backend="mlx",
            runtime_type="provider",
        )
        decision = resolve_provider_for_context(ctx, registry)

        assert decision.source == "registry"
        assert decision.artifact_id == record.artifact_id
        assert decision.fallback_used is False

    def test_fallback_when_no_model(self, tmp_path: Path) -> None:
        """Without any published model, routing falls back to frontier."""
        from autocontext.providers.scenario_routing import (
            ScenarioRoutingContext,
            resolve_provider_for_context,
        )
        from autocontext.training.model_registry import ModelRegistry

        registry = ModelRegistry(tmp_path)
        ctx = ScenarioRoutingContext(scenario="grid_ctf", backend="mlx")

        decision = resolve_provider_for_context(
            ctx, registry,
            fallback_provider="anthropic",
            fallback_model="claude-sonnet-4-20250514",
        )

        assert decision.fallback_used is True
        assert decision.source == "fallback"
        assert decision.provider_type == "anthropic"

    def test_multi_scenario_isolation(self, tmp_path: Path) -> None:
        """Models for different scenarios don't interfere."""
        from autocontext.providers.scenario_routing import (
            ScenarioRoutingContext,
            resolve_provider_for_context,
        )
        from autocontext.training.model_registry import (
            ModelRegistry,
            TrainingCompletionOutput,
            publish_training_output,
        )

        registry = ModelRegistry(tmp_path)

        # Publish grid_ctf model
        grid_record = publish_training_output(
            TrainingCompletionOutput(
                run_id="train-grid", checkpoint_path="/models/grid",
                backend="mlx", scenario="grid_ctf",
            ),
            registry, auto_activate=True,
        )

        # Publish othello model
        othello_record = publish_training_output(
            TrainingCompletionOutput(
                run_id="train-othello", checkpoint_path="/models/othello",
                backend="mlx", scenario="othello",
            ),
            registry, auto_activate=True,
        )

        # Resolve for each scenario
        grid_decision = resolve_provider_for_context(
            ScenarioRoutingContext(scenario="grid_ctf", backend="mlx"),
            registry,
        )
        othello_decision = resolve_provider_for_context(
            ScenarioRoutingContext(scenario="othello", backend="mlx"),
            registry,
        )

        assert grid_decision.artifact_id == grid_record.artifact_id
        assert othello_decision.artifact_id == othello_record.artifact_id
        assert grid_decision.artifact_id != othello_decision.artifact_id
