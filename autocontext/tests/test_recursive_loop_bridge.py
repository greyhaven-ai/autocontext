"""Recursive-loop serving bridge: a trained adapter is served by the right local client.

Closes the last wiring step of the loop for capable models. CI-safe (JSON-file registry,
no MLX load, no training): exercises the pure routing decision (``plan_local_client``), the
record resolution preference, and the publish -> route round-trip that the runner relies on.
"""

from __future__ import annotations

from autocontext.agents.scenario_bound_clients import (
    LocalClientPlan,
    _resolve_local_record,
    plan_local_client,
)
from autocontext.config.settings import AppSettings
from autocontext.training.backends import default_backend_registry
from autocontext.training.model_registry import (
    DistilledModelRecord,
    ModelRegistry,
    TrainingCompletionOutput,
    publish_training_output,
)


def _record(*, backend: str, checkpoint: str = "/ckpt", metadata: dict | None = None) -> DistilledModelRecord:
    return DistilledModelRecord(
        artifact_id="a1",
        scenario="grid_ctf",
        scenario_family="game",
        backend=backend,
        checkpoint_path=checkpoint,
        runtime_types=["provider"],
        activation_state="active",
        training_metrics={},
        provenance={},
        metadata=metadata or {},
    )


def _register_active(
    root,
    *,
    artifact_id: str,
    backend: str,
    checkpoint: str,
    metadata: dict | None = None,
    runtime_types: list[str] | None = None,
) -> None:
    # Default to the *real* runtime slots the backend advertises, so the test exercises the
    # actual runner publish shape rather than a hand-picked ["provider"] that masks slot bugs.
    if runtime_types is None:
        runtime_types = default_backend_registry().get(backend).supported_runtime_types()
    reg = ModelRegistry(root)
    reg.register(
        DistilledModelRecord(
            artifact_id=artifact_id,
            scenario="grid_ctf",
            scenario_family="game",
            backend=backend,
            checkpoint_path=checkpoint,
            runtime_types=runtime_types,
            activation_state="candidate",
            training_metrics={},
            provenance={},
            metadata=metadata or {},
        )
    )
    reg.activate(artifact_id)


# --- plan_local_client: the pure routing decision -------------------------------------------


def test_mlx_full_checkpoint_serves_directly() -> None:
    plan = plan_local_client(_record(backend="mlx", checkpoint="/trained/gpt"))
    assert plan == LocalClientPlan(kind="mlx", model="/trained/gpt", adapter_path=None, score_conditioned=False)


def test_mlxlm_adapter_routes_to_base_plus_adapter() -> None:
    plan = plan_local_client(_record(backend="mlxlm", checkpoint="/adapters/lora", metadata={"base_model": "Qwen/Qwen2.5-1.5B"}))
    assert plan == LocalClientPlan(
        kind="mlxlm", model="Qwen/Qwen2.5-1.5B", adapter_path="/adapters/lora", score_conditioned=False
    )


def test_opd_adapter_routes_like_mlxlm() -> None:
    plan = plan_local_client(_record(backend="opd", checkpoint="/adapters/opd", metadata={"base_model": "Qwen/Qwen2.5-3B"}))
    assert plan is not None
    assert plan.kind == "mlxlm"
    assert plan.model == "Qwen/Qwen2.5-3B"
    assert plan.adapter_path == "/adapters/opd"


def test_score_conditioned_flag_is_carried_for_serving() -> None:
    plan = plan_local_client(_record(backend="opd", metadata={"base_model": "Qwen/Qwen2.5-3B", "score_conditioned": True}))
    assert plan is not None and plan.score_conditioned is True


def test_adapter_without_base_model_is_unservable() -> None:
    # An adapter is useless without the base it was trained against -> fall back, not crash.
    assert plan_local_client(_record(backend="mlxlm", metadata={})) is None


def test_unknown_backend_falls_back() -> None:
    assert plan_local_client(_record(backend="something-else")) is None


# --- _resolve_local_record: which active model wins -----------------------------------------


def test_resolves_active_record_for_scenario(tmp_path) -> None:
    _register_active(
        tmp_path, artifact_id="m1", backend="mlxlm", checkpoint="/lora", metadata={"base_model": "Qwen/Qwen2.5-1.5B"}
    )
    settings = AppSettings(agent_provider="mlx", mlx_model_path="", knowledge_root=tmp_path)
    record = _resolve_local_record(settings, "grid_ctf")
    assert record is not None and record.backend == "mlxlm"


def test_adapter_backend_preferred_over_from_scratch_gpt(tmp_path) -> None:
    # Both active for the scenario: the capable instruct fine-tune wins over the from-scratch GPT.
    _register_active(tmp_path, artifact_id="gpt", backend="mlx", checkpoint="/gpt")
    _register_active(tmp_path, artifact_id="ad", backend="opd", checkpoint="/lora", metadata={"base_model": "Qwen/Qwen2.5-3B"})
    settings = AppSettings(agent_provider="mlx", mlx_model_path="", knowledge_root=tmp_path)
    record = _resolve_local_record(settings, "grid_ctf")
    assert record is not None and record.backend == "opd"


def test_no_active_model_resolves_to_none(tmp_path) -> None:
    settings = AppSettings(agent_provider="mlx", mlx_model_path="", knowledge_root=tmp_path)
    assert _resolve_local_record(settings, "grid_ctf") is None


# --- publish -> route round-trip: what the runner actually wires up --------------------------


def test_published_adapter_record_routes_to_mlxlm(tmp_path) -> None:
    """The runner records base_model + score_conditioned in completion.metadata; publishing must
    surface them on the record so the resolver can rebuild MLXLMClient(base, adapter, ...)."""
    registry = ModelRegistry(tmp_path)
    completion = TrainingCompletionOutput(
        run_id="r1",
        checkpoint_path="/adapters/lora",
        backend="opd",
        scenario="grid_ctf",
        scenario_family="game",
        # Real runner shape: the backend's advertised slots, not a hand-picked ["provider"].
        runtime_types=default_backend_registry().get("opd").supported_runtime_types(),
        metadata={"base_model": "Qwen/Qwen2.5-3B", "score_conditioned": True},
    )
    record = publish_training_output(completion, registry, artifacts_root=None, auto_activate=True)

    assert record.metadata.get("base_model") == "Qwen/Qwen2.5-3B"
    assert record.metadata.get("score_conditioned") is True

    plan = plan_local_client(record)
    assert plan is not None
    assert plan.kind == "mlxlm"
    assert plan.model == "Qwen/Qwen2.5-3B"
    assert plan.adapter_path == "/adapters/lora"
    assert plan.score_conditioned is True


# --- regression: the two ways the runner path was unservable --------------------------------


def test_adapter_backends_advertise_provider_serving() -> None:
    """[P1] An adapter published with the backend's own runtime slots must be resolvable as a
    provider. Adapter backends advertised only ["checkpoint"], so resolve_model(provider) missed
    every real runner record while ["provider"]-only tests passed."""
    registry = default_backend_registry()
    for name in ("mlxlm", "opd", "grpo"):
        assert "provider" in registry.get(name).supported_runtime_types(), name


def test_adapter_backends_expose_effective_default_base_model() -> None:
    """[P2] A default `autoctx train --backend opd/mlxlm` leaves config.base_model empty and the
    subprocess applies the backend default; that effective base must be recordable, else the
    published adapter is unservable even with the runtime-slot fixed."""
    registry = default_backend_registry()
    for name in ("mlxlm", "opd", "grpo"):
        assert registry.get(name).default_base_model(), name
    # From-scratch backends train no adapter and need no base.
    assert default_backend_registry().get("mlx").default_base_model() == ""


def test_default_train_publish_is_servable(tmp_path) -> None:
    """[P1+P2] End-to-end of the *default* CLI path: no --base-model override, backend-advertised
    runtime slots, effective default base recorded -> the record resolves and routes to MLXLMClient."""
    backend = default_backend_registry().get("opd")
    effective_base = backend.default_base_model()  # what runner records when config.base_model == ""
    _register_active(
        tmp_path,
        artifact_id="m1",
        backend="opd",
        checkpoint="/adapters/lora",
        metadata={"base_model": effective_base, "score_conditioned": False},
        # runtime_types defaults to backend.supported_runtime_types() inside the helper
    )
    settings = AppSettings(agent_provider="mlx", mlx_model_path="", knowledge_root=tmp_path)

    record = _resolve_local_record(settings, "grid_ctf")
    assert record is not None, "default-trained adapter must resolve as a provider"
    plan = plan_local_client(record)
    assert plan is not None and plan.kind == "mlxlm"
    assert plan.model == effective_base and plan.model != ""
    assert plan.adapter_path == "/adapters/lora"
