"""Scenario-bound agent client resolution, extracted from orchestrator.py for module size.

These resolve a per-scenario agent client at role-execution time, once the scenario is known
(the orchestrator itself is built before the scenario): the MLX recursive-loop model from the
registry, and pi / pi-rpc runtime handoffs. Functions take the orchestrator as ``orch`` so
they reuse its routed-client cache and client wrapping.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from autocontext.agents.llm_client import (
    LanguageModelClient,
    MLXClient,
    MLXLMClient,
    build_client_from_settings,
)
from autocontext.agents.role_runtime_overrides import settings_for_budgeted_role_call

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.config import AppSettings
    from autocontext.training.model_registry import DistilledModelRecord

logger = logging.getLogger(__name__)

# Backends whose checkpoint is a standalone model served directly by MLXClient
# (from-scratch GPT / full fine-tunes), vs. backends whose checkpoint is a LoRA
# adapter that must be loaded on top of its base model.
_FULL_CHECKPOINT_BACKENDS = {"mlx"}
# mlx-lm LoRA adapter backends served as base + adapter via MLXLMClient (Apple Silicon).
_MLX_ADAPTER_BACKENDS = {"mlxlm", "opd", "grpo"}
# torch/peft LoRA adapter backends served as base + adapter via SftTorchClient (CUDA, cuda extra).
_TORCH_ADAPTER_BACKENDS = {"sft"}
# All LoRA adapter backends: they need the base model recorded at publish time to be servable.
_ADAPTER_BACKENDS = _MLX_ADAPTER_BACKENDS | _TORCH_ADAPTER_BACKENDS


@dataclass(frozen=True)
class LocalClientPlan:
    """How to rebuild a served client from a trained registry record.

    ``kind`` selects the client class; ``model`` is the checkpoint path (full) or the
    base model id (adapter); ``adapter_path`` and ``score_conditioned`` apply to adapters.
    """

    kind: str  # "mlx" | "mlxlm" | "sft"
    model: str
    adapter_path: str | None
    score_conditioned: bool


def plan_local_client(record: DistilledModelRecord) -> LocalClientPlan | None:
    """Decide which local client serves a trained record (pure; no model loading).

    Full-checkpoint backends (``mlx``) serve their checkpoint directly. Adapter backends
    (``mlxlm`` / ``opd`` / ``sft``) need the base model they trained against (recorded in metadata
    at publish time); mlx adapters also carry the score-conditioning flag so inference re-applies
    the quality prefix, while torch/peft ``sft`` adapters serve via ``SftTorchClient``. Returns
    ``None`` for an unknown backend or an adapter record missing its base model, so the caller
    falls back to the default client rather than serving something broken.
    """
    backend = (record.backend or "").lower()
    if backend in _ADAPTER_BACKENDS:
        base_model = str(record.metadata.get("base_model") or "")
        if not base_model:
            logger.debug("agents.scenario_bound_clients: adapter record %s has no base_model", record.artifact_id)
            return None
        kind = "sft" if backend in _TORCH_ADAPTER_BACKENDS else "mlxlm"
        return LocalClientPlan(
            kind=kind,
            model=base_model,
            adapter_path=record.checkpoint_path,
            score_conditioned=bool(record.metadata.get("score_conditioned")),
        )
    if backend in _FULL_CHECKPOINT_BACKENDS:
        return LocalClientPlan(kind="mlx", model=record.checkpoint_path, adapter_path=None, score_conditioned=False)
    return None


def _resolve_local_record(settings: AppSettings, scenario_name: str) -> DistilledModelRecord | None:
    """Active provider-runtime record the harness trained for this scenario, capable first.

    Adapter backends (instruct fine-tunes) are preferred over the from-scratch GPT when both
    are active for the scenario. Returns ``None`` on any registry error or no active model.
    """
    from autocontext.training.model_registry import ModelRegistry, resolve_model

    try:
        registry = ModelRegistry(settings.knowledge_root)
    except Exception:
        logger.debug("agents.scenario_bound_clients: could not open model registry", exc_info=True)
        return None
    for backend in ("opd", "mlxlm", "grpo", "sft", "mlx"):
        try:
            record = resolve_model(registry, scenario=scenario_name, backend=backend, runtime_type="provider")
        except Exception:
            logger.debug("agents.scenario_bound_clients: resolve_model failed for backend %s", backend, exc_info=True)
            record = None
        if record is not None:
            return record
    return None


def build_planned_client(plan: LocalClientPlan, settings: AppSettings) -> LanguageModelClient:
    """Construct the MLX / MLXLM / SFT-torch client described by ``plan`` (loads the model)."""
    if plan.kind == "sft":
        # Lazy import keeps torch (the optional ``cuda`` extra) out of module import time; a
        # torch-absent environment raises here and the caller falls back to the frontier client.
        from autocontext.agents.sft_torch_client import SftTorchClient

        return SftTorchClient(
            plan.model,
            adapter_path=plan.adapter_path,
            temperature=settings.mlx_temperature,
            max_tokens=settings.mlx_max_tokens,
        )
    if plan.kind == "mlxlm":
        return MLXLMClient(
            plan.model,
            adapter_path=plan.adapter_path,
            temperature=settings.mlx_temperature,
            max_tokens=settings.mlx_max_tokens,
            score_conditioned=plan.score_conditioned,
        )
    return MLXClient(
        model_path=plan.model,
        temperature=settings.mlx_temperature,
        max_tokens=settings.mlx_max_tokens,
    )


def scenario_bound_mlx_client(orch: AgentOrchestrator, role: str, *, scenario_name: str) -> LanguageModelClient | None:
    """Build the local agent client resolved from the registry for this scenario (recursive loop).

    An explicit ``AUTOCONTEXT_MLX_MODEL_PATH`` still wins (served as a full checkpoint). Otherwise
    the active trained record is resolved and routed by backend: ``mlx`` checkpoints serve directly,
    while ``mlxlm`` / ``opd`` adapters rebuild ``MLXLMClient(base, adapter, score_conditioned=...)``.
    Returns ``None`` when nothing is resolvable, so the caller falls back to the default client.
    """
    key: tuple[str, str | None, str, str]
    if orch.settings.mlx_model_path:
        key = ("mlx", None, scenario_name, role)
        cached = orch._routed_clients.get(key)
        if cached is not None:
            return cached
        try:
            client = build_client_from_settings(orch.settings, scenario_name=scenario_name)
        except Exception:
            logger.debug("agents.scenario_bound_clients: explicit mlx path build failed", exc_info=True)
            return None
        client = orch._wrap_client(client, provider_name=f"mlx:{role}")
        orch._routed_clients[key] = client
        return client

    record = _resolve_local_record(orch.settings, scenario_name)
    if record is None:
        return None
    plan = plan_local_client(record)
    if plan is None:
        return None

    key = (plan.kind, plan.adapter_path, scenario_name, role)
    cached = orch._routed_clients.get(key)
    if cached is not None:
        return cached
    try:
        client = build_planned_client(plan, orch.settings)
    except Exception:
        logger.debug("agents.scenario_bound_clients: planned client build failed", exc_info=True)
        return None
    client = orch._wrap_client(client, provider_name=f"{plan.kind}:{role}")
    orch._routed_clients[key] = client
    return client


def scenario_bound_runtime_client(
    orch: AgentOrchestrator, provider_type: str, role: str, *, scenario_name: str
) -> LanguageModelClient | None:
    """Resolve a scenario-bound client for the given provider, or ``None`` to fall back.

    ``mlx`` resolves the harness-trained model from the registry; ``pi`` / ``pi-rpc`` resolve
    a runtime handoff. Other providers have no scenario-bound form.
    """
    if not scenario_name:
        return None
    if provider_type == "mlx":
        return scenario_bound_mlx_client(orch, role, scenario_name=scenario_name)
    if provider_type not in {"pi", "pi-rpc"}:
        return None

    from autocontext.agents.provider_bridge import create_role_client

    call_settings, is_budgeted = settings_for_budgeted_role_call(
        orch.settings, provider_type, role, orch._active_generation_deadline
    )
    key = (provider_type.lower(), None, scenario_name, role)
    if not is_budgeted:
        cached = orch._routed_clients.get(key)
        if cached is not None:
            return cached

    client = create_role_client(provider_type, call_settings, scenario_name=scenario_name, role=role)
    if client is not None:
        client = orch._wrap_client(client, provider_name=f"{provider_type}:{role}")
        if is_budgeted:
            orch._mark_disposable_client(client)
        else:
            orch._routed_clients[key] = client
    return client
