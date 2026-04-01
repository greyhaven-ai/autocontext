"""Scenario-aware provider routing and Pi runtime handoff (AC-289 + AC-290).

Resolves the correct local/frontier provider from the distilled-model
registry using scenario/task/runtime context. Defines the Pi runtime
handoff contract for scenario-local model selection.

AC-289: ScenarioRoutingContext, RoutingDecision, resolve_provider_for_context
AC-290: PiModelHandoff, resolve_pi_model, PiExecutionTrace
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from autocontext.training.model_registry import ModelRegistry, resolve_model


@dataclass(slots=True)
class ScenarioRoutingContext:
    """Context for scenario-aware provider resolution."""

    scenario: str
    scenario_family: str = ""
    role: str = ""
    backend: str = ""
    runtime_type: str = "provider"
    manual_model_override: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoutingDecision(BaseModel):
    """Resolved provider/model choice with provenance."""

    provider_type: str  # mlx, anthropic, openai-compatible, etc.
    model: str
    artifact_id: str | None
    source: str  # registry, manual_override, fallback
    fallback_used: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingDecision:
        return cls.model_validate(data)


def _resolve_backend_provider_type(backend: str) -> str:
    """Map a local-backend name to the concrete provider type the runtime expects."""
    normalized = backend.strip().lower()
    return normalized or "mlx"


def resolve_provider_for_context(
    ctx: ScenarioRoutingContext,
    registry: ModelRegistry,
    fallback_provider: str = "anthropic",
    fallback_model: str = "",
) -> RoutingDecision:
    """Resolve provider/model for a scenario routing context.

    Priority: manual override → active registry entry → fallback.
    """
    base_metadata: dict[str, Any] = {
        "scenario": ctx.scenario,
        "scenario_family": ctx.scenario_family,
        "role": ctx.role,
        "backend": ctx.backend,
        "runtime_type": ctx.runtime_type,
    }

    # 1. Manual override
    if ctx.manual_model_override:
        return RoutingDecision(
            provider_type=_resolve_backend_provider_type(ctx.backend),
            model=ctx.manual_model_override,
            artifact_id=None,
            source="manual_override",
            fallback_used=False,
            metadata=base_metadata,
        )

    # 2. Registry lookup
    record = resolve_model(
        registry,
        scenario=ctx.scenario,
        backend=ctx.backend,
        runtime_type=ctx.runtime_type,
    )
    if record is not None:
        return RoutingDecision(
            provider_type=_resolve_backend_provider_type(record.backend or ctx.backend),
            model=record.checkpoint_path,
            artifact_id=record.artifact_id,
            source="registry",
            fallback_used=False,
            metadata={**base_metadata, "training_metrics": record.training_metrics},
        )

    # 3. Fallback
    return RoutingDecision(
        provider_type=fallback_provider,
        model=fallback_model,
        artifact_id=None,
        source="fallback",
        fallback_used=True,
        metadata=base_metadata,
    )


# ---------------------------------------------------------------------------
# AC-290: Pi runtime handoff
# ---------------------------------------------------------------------------


class PiModelHandoff(BaseModel):
    """Contract for handing a resolved model to a Pi runtime."""

    artifact_id: str
    checkpoint_path: str
    backend: str
    scenario: str
    load_descriptor: str  # e.g. "mlx://grid_ctf/pi-v1"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PiModelHandoff:
        return cls.model_validate(data)


def resolve_pi_model(
    registry: ModelRegistry,
    scenario: str,
    backend: str = "mlx",
    manual_override: str | None = None,
) -> PiModelHandoff | None:
    """Resolve a Pi model for a scenario using the registry.

    Returns None if no active Pi model exists and no override provided.
    """
    if manual_override:
        return PiModelHandoff(
            artifact_id="manual",
            checkpoint_path=manual_override,
            backend=backend,
            scenario=scenario,
            load_descriptor=f"{backend}://{manual_override}",
        )

    record = resolve_model(
        registry,
        scenario=scenario,
        backend=backend,
        runtime_type="pi",
    )
    if record is None:
        return None

    return PiModelHandoff(
        artifact_id=record.artifact_id,
        checkpoint_path=record.checkpoint_path,
        backend=record.backend,
        scenario=record.scenario,
        load_descriptor=f"{record.backend}://{record.scenario}/{record.artifact_id}",
    )


class PiExecutionTrace(BaseModel):
    """Trace of which Pi model/config actually ran."""

    scenario: str
    artifact_id: str
    checkpoint_path: str
    backend: str
    resolved_via: str  # registry, manual_override, fallback
    success: bool
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PiExecutionTrace:
        return cls.model_validate(data)
