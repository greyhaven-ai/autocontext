"""Tests for AC-289 + AC-290: scenario-aware provider routing and Pi handoff.

AC-289: RoutingDecision, ScenarioRoutingContext, resolve_provider_for_context
AC-290: PiModelHandoff, resolve_pi_model, PiExecutionTrace
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _registry_with_models(tmp_path: Path) -> Any:
    from autocontext.training.model_registry import (
        DistilledModelRecord,
        ModelRegistry,
    )

    registry = ModelRegistry(tmp_path)
    registry.register(DistilledModelRecord(
        artifact_id="grid-mlx-1",
        scenario="grid_ctf",
        scenario_family="game",
        backend="mlx",
        checkpoint_path="/models/grid_ctf/mlx-v1",
        runtime_types=["provider"],
        activation_state="active",
        training_metrics={"loss": 0.4},
        provenance={"run_id": "train-1"},
    ))
    registry.register(DistilledModelRecord(
        artifact_id="grid-pi-1",
        scenario="grid_ctf",
        scenario_family="game",
        backend="mlx",
        checkpoint_path="/models/grid_ctf/pi-v1",
        runtime_types=["pi"],
        activation_state="active",
        training_metrics={"loss": 0.35},
        provenance={"run_id": "train-2"},
    ))
    registry.register(DistilledModelRecord(
        artifact_id="othello-mlx-1",
        scenario="othello",
        scenario_family="game",
        backend="mlx",
        checkpoint_path="/models/othello/mlx-v1",
        runtime_types=["provider"],
        activation_state="candidate",
        training_metrics={},
        provenance={},
    ))
    return registry


# ===========================================================================
# AC-289: ScenarioRoutingContext
# ===========================================================================


class TestScenarioRoutingContext:
    def test_construction(self) -> None:
        from autocontext.providers.scenario_routing import ScenarioRoutingContext

        ctx = ScenarioRoutingContext(
            scenario="grid_ctf",
            scenario_family="game",
            role="competitor",
            backend="mlx",
        )
        assert ctx.scenario == "grid_ctf"
        assert ctx.role == "competitor"

    def test_defaults(self) -> None:
        from autocontext.providers.scenario_routing import ScenarioRoutingContext

        ctx = ScenarioRoutingContext(scenario="grid_ctf")
        assert ctx.backend == ""
        assert ctx.role == ""
        assert ctx.runtime_type == "provider"


# ===========================================================================
# AC-289: RoutingDecision
# ===========================================================================


class TestRoutingDecision:
    def test_construction(self) -> None:
        from autocontext.providers.scenario_routing import RoutingDecision

        dec = RoutingDecision(
            provider_type="mlx",
            model="grid-mlx-1",
            artifact_id="grid-mlx-1",
            source="registry",
            fallback_used=False,
        )
        assert dec.provider_type == "mlx"
        assert dec.source == "registry"

    def test_roundtrip(self) -> None:
        from autocontext.providers.scenario_routing import RoutingDecision

        dec = RoutingDecision(
            provider_type="anthropic",
            model="claude-sonnet-4-20250514",
            artifact_id=None,
            source="fallback",
            fallback_used=True,
        )
        d = dec.to_dict()
        restored = RoutingDecision.from_dict(d)
        assert restored.fallback_used is True
        assert restored.source == "fallback"


# ===========================================================================
# AC-289: resolve_provider_for_context
# ===========================================================================


class TestResolveProviderForContext:
    def test_resolves_active_local_model(self, tmp_path: Path) -> None:
        from autocontext.providers.scenario_routing import (
            ScenarioRoutingContext,
            resolve_provider_for_context,
        )

        registry = _registry_with_models(tmp_path)
        ctx = ScenarioRoutingContext(
            scenario="grid_ctf", backend="mlx", runtime_type="provider",
        )
        decision = resolve_provider_for_context(ctx, registry)

        assert decision.artifact_id == "grid-mlx-1"
        assert decision.provider_type == "mlx"
        assert decision.source == "registry"
        assert decision.fallback_used is False

    def test_falls_back_when_no_active(self, tmp_path: Path) -> None:
        from autocontext.providers.scenario_routing import (
            ScenarioRoutingContext,
            resolve_provider_for_context,
        )

        registry = _registry_with_models(tmp_path)
        ctx = ScenarioRoutingContext(
            scenario="othello", backend="mlx", runtime_type="provider",
        )
        decision = resolve_provider_for_context(
            ctx, registry, fallback_provider="anthropic", fallback_model="claude-sonnet-4-20250514",
        )

        assert decision.fallback_used is True
        assert decision.source == "fallback"
        assert decision.provider_type == "anthropic"

    def test_manual_override_wins(self, tmp_path: Path) -> None:
        from autocontext.providers.scenario_routing import (
            ScenarioRoutingContext,
            resolve_provider_for_context,
        )

        registry = _registry_with_models(tmp_path)
        ctx = ScenarioRoutingContext(
            scenario="grid_ctf", backend="mlx",
            manual_model_override="/custom/model/path",
        )
        decision = resolve_provider_for_context(ctx, registry)

        assert decision.provider_type == "mlx"
        assert decision.source == "manual_override"
        assert decision.model == "/custom/model/path"

    def test_captures_scenario_in_decision(self, tmp_path: Path) -> None:
        from autocontext.providers.scenario_routing import (
            ScenarioRoutingContext,
            resolve_provider_for_context,
        )

        registry = _registry_with_models(tmp_path)
        ctx = ScenarioRoutingContext(scenario="grid_ctf", backend="mlx")
        decision = resolve_provider_for_context(ctx, registry)

        assert decision.metadata.get("scenario") == "grid_ctf"


# ===========================================================================
# AC-290: PiModelHandoff
# ===========================================================================


class TestPiModelHandoff:
    def test_construction(self) -> None:
        from autocontext.providers.scenario_routing import PiModelHandoff

        handoff = PiModelHandoff(
            artifact_id="grid-pi-1",
            checkpoint_path="/models/grid_ctf/pi-v1",
            backend="mlx",
            scenario="grid_ctf",
            load_descriptor="mlx://grid_ctf/pi-v1",
        )
        assert handoff.artifact_id == "grid-pi-1"
        assert handoff.load_descriptor == "mlx://grid_ctf/pi-v1"

    def test_roundtrip(self) -> None:
        from autocontext.providers.scenario_routing import PiModelHandoff

        handoff = PiModelHandoff(
            artifact_id="a1", checkpoint_path="/p", backend="mlx",
            scenario="grid_ctf", load_descriptor="mlx://a1",
        )
        d = handoff.to_dict()
        restored = PiModelHandoff.from_dict(d)
        assert restored.artifact_id == "a1"


# ===========================================================================
# AC-290: resolve_pi_model
# ===========================================================================


class TestResolvePiModel:
    def test_resolves_pi_runtime_model(self, tmp_path: Path) -> None:
        from autocontext.providers.scenario_routing import resolve_pi_model

        registry = _registry_with_models(tmp_path)
        handoff = resolve_pi_model(registry, scenario="grid_ctf", backend="mlx")

        assert handoff is not None
        assert handoff.artifact_id == "grid-pi-1"
        assert handoff.checkpoint_path == "/models/grid_ctf/pi-v1"

    def test_returns_none_when_no_pi_model(self, tmp_path: Path) -> None:
        from autocontext.providers.scenario_routing import resolve_pi_model

        registry = _registry_with_models(tmp_path)
        handoff = resolve_pi_model(registry, scenario="othello", backend="mlx")

        assert handoff is None

    def test_manual_override(self, tmp_path: Path) -> None:
        from autocontext.providers.scenario_routing import resolve_pi_model

        registry = _registry_with_models(tmp_path)
        handoff = resolve_pi_model(
            registry, scenario="grid_ctf", backend="mlx",
            manual_override="/custom/pi/model",
        )

        assert handoff is not None
        assert handoff.checkpoint_path == "/custom/pi/model"


# ===========================================================================
# AC-290: PiExecutionTrace
# ===========================================================================


class TestPiExecutionTrace:
    def test_construction(self) -> None:
        from autocontext.providers.scenario_routing import PiExecutionTrace

        trace = PiExecutionTrace(
            scenario="grid_ctf",
            artifact_id="grid-pi-1",
            checkpoint_path="/models/grid_ctf/pi-v1",
            backend="mlx",
            resolved_via="registry",
            success=True,
        )
        assert trace.resolved_via == "registry"
        assert trace.success is True

    def test_roundtrip(self) -> None:
        from autocontext.providers.scenario_routing import PiExecutionTrace

        trace = PiExecutionTrace(
            scenario="test", artifact_id="a1",
            checkpoint_path="/p", backend="mlx",
            resolved_via="manual_override", success=False,
            error="Model load failed",
        )
        d = trace.to_dict()
        restored = PiExecutionTrace.from_dict(d)
        assert restored.success is False
        assert restored.error == "Model load failed"
