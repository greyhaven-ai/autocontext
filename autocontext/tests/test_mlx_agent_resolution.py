"""Recursive loop: the MLX agent provider resolves the model the harness trained + activated.

CI-safe (JSON-file registry, no MLX): exercises _resolve_mlx_model_path, the seam that lets a
run use a local model a prior run produced, without constructing the MLX-loading client.
"""

from __future__ import annotations

import pytest

from autocontext.agents.llm_client import _resolve_mlx_model_path
from autocontext.config.settings import AppSettings
from autocontext.training.model_registry import DistilledModelRecord, ModelRegistry


def _register_active(root, *, artifact_id: str, scenario: str, checkpoint: str, backend: str = "mlx") -> None:
    reg = ModelRegistry(root)
    reg.register(
        DistilledModelRecord(
            artifact_id=artifact_id,
            scenario=scenario,
            scenario_family="game",
            backend=backend,
            checkpoint_path=checkpoint,
            runtime_types=["provider"],
            activation_state="candidate",
            training_metrics={},
            provenance={},
        )
    )
    reg.activate(artifact_id)


def test_explicit_path_takes_precedence(tmp_path) -> None:
    settings = AppSettings(agent_provider="mlx", mlx_model_path="/explicit/model", knowledge_root=tmp_path)
    # Even with an active registry model present, the explicit env path wins.
    _register_active(tmp_path, artifact_id="m1", scenario="grid_ctf", checkpoint="/trained/ckpt")
    assert _resolve_mlx_model_path(settings, "grid_ctf") == "/explicit/model"


def test_resolves_active_registry_model_when_no_explicit_path(tmp_path) -> None:
    """The loop-closing case: no env path, but the harness trained+activated a model."""
    _register_active(tmp_path, artifact_id="m1", scenario="grid_ctf", checkpoint="/trained/ckpt")
    settings = AppSettings(agent_provider="mlx", mlx_model_path="", knowledge_root=tmp_path)
    assert _resolve_mlx_model_path(settings, "grid_ctf") == "/trained/ckpt"


def test_only_resolves_mlx_backend_full_checkpoints(tmp_path) -> None:
    """A non-mlx backend (e.g. an opd/mlxlm adapter) is not picked as an MLXProvider checkpoint."""
    _register_active(tmp_path, artifact_id="a1", scenario="grid_ctf", checkpoint="/adapter", backend="opd")
    settings = AppSettings(agent_provider="mlx", mlx_model_path="", knowledge_root=tmp_path)
    with pytest.raises(ValueError, match="MLX_MODEL_PATH"):
        _resolve_mlx_model_path(settings, "grid_ctf")


def test_raises_when_no_path_and_no_active_model(tmp_path) -> None:
    settings = AppSettings(agent_provider="mlx", mlx_model_path="", knowledge_root=tmp_path)
    with pytest.raises(ValueError, match="MLX_MODEL_PATH"):
        _resolve_mlx_model_path(settings, "grid_ctf")


def test_deferred_mlx_client_raises_only_on_use() -> None:
    """The deferred default client constructs fine (no MLX load) and errors only if invoked."""
    from autocontext.agents.llm_client import DeferredMLXClient

    client = DeferredMLXClient()  # construction must not raise
    with pytest.raises(ValueError, match="MLX_MODEL_PATH"):
        client.generate(model="m", prompt="p", max_tokens=1, temperature=0.0)


def test_orchestrator_constructs_without_crash_for_mlx_without_path(tmp_path) -> None:
    """The P1 fix: the scenario-agnostic orchestrator is built before the scenario is known,
    so AUTOCONTEXT_AGENT_PROVIDER=mlx with no path must NOT crash at construction (it defers)."""
    from autocontext.agents.orchestrator import AgentOrchestrator

    settings = AppSettings(
        agent_provider="mlx",
        mlx_model_path="",
        knowledge_root=tmp_path,
        db_path=tmp_path / "runs.db",
    )
    orch = AgentOrchestrator.from_settings(settings)  # previously raised here
    assert orch is not None
