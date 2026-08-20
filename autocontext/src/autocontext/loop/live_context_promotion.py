"""Production composition for matched context-bundle promotion."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from autocontext.context_bundles.false_promotion import (
    CampaignFalsePromotionController,
    CampaignFalsePromotionPolicy,
    FalsePromotionMethod,
)
from autocontext.context_bundles.models import MAX_SAFE_INTEGER, ConfirmationPolicy, TrialLane, stable_digest
from autocontext.context_bundles.promotion import (
    ContextBundleEvaluationUnit,
    ContextBundleLifecycleAudit,
    ContextBundlePromotionCoordinator,
)
from autocontext.context_bundles.runtime_evaluator import (
    build_runtime_context_bundle_evaluator,
    materialize_runtime_fixture,
)
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.harness.evaluation.types import EvaluationLimits


@dataclass(frozen=True, slots=True)
class LiveContextPromotionConfig:
    min_screen_pairs: int = 2
    min_confirmation_pairs: int = 6
    max_confirmation_pairs: int = 20
    min_heldout_pairs: int = 2
    min_effect: float = 0.0
    confidence_z: float = 1.96
    seed_base: int = 50_000
    timeout_seconds: float = 30.0
    max_memory_mb: int = 512
    familywise_alpha: float = 0.05
    allocation_decay: float = 0.5
    min_independent_confirmation_blocks: int = 2
    robust_method: FalsePromotionMethod = "cluster_t"

    def confirmation_policy(self) -> ConfirmationPolicy:
        return ConfirmationPolicy(
            min_screen_pairs=self.min_screen_pairs,
            min_confirmation_pairs=self.min_confirmation_pairs,
            max_confirmation_pairs=self.max_confirmation_pairs,
            min_heldout_pairs=self.min_heldout_pairs,
            min_effect=self.min_effect,
            confidence_z=self.confidence_z,
        )

    def false_promotion_policy(self) -> CampaignFalsePromotionPolicy:
        return CampaignFalsePromotionPolicy(
            familywise_alpha=self.familywise_alpha,
            allocation_decay=self.allocation_decay,
            min_independent_confirmation_blocks=self.min_independent_confirmation_blocks,
            robust_method=self.robust_method,
        )


def build_live_context_promotion(
    *,
    scenario_name: str,
    scenario: object,
    run_id: str,
    cohort_id: str,
    evaluator_epoch: str,
    store: ContextBundleStore,
    orchestrator: object,
    supervisor: object,
    risk_root: Path,
    config: LiveContextPromotionConfig,
    hook_bus: object | None = None,
    lifecycle_auditor: ContextBundleLifecycleAudit | None = None,
    generation_index: int = 0,
) -> ContextBundlePromotionCoordinator:
    """Build the live coordinator with a deterministic, predeclared unit plan."""

    # Imports remain runtime-typed to avoid making the context package import
    # the generation/orchestrator graph during normal model-only use.
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.execution.supervisor import ExecutionSupervisor
    from autocontext.extensions import HookBus
    from autocontext.scenarios.base import ScenarioInterface

    if not isinstance(scenario, ScenarioInterface):
        raise TypeError("live context promotion requires a ScenarioInterface")
    if not isinstance(orchestrator, AgentOrchestrator):
        raise TypeError("live context promotion requires an AgentOrchestrator")
    if not isinstance(supervisor, ExecutionSupervisor):
        raise TypeError("live context promotion requires an ExecutionSupervisor")
    if hook_bus is not None and not isinstance(hook_bus, HookBus):
        raise TypeError("live context promotion hook bus has the wrong type")
    if not run_id.strip() or not cohort_id.strip():
        raise ValueError("live context promotion requires campaign and cohort identities")
    policy = config.confirmation_policy()
    cohort = f"{run_id}:{evaluator_epoch}:{cohort_id}"
    units = _evaluation_units(scenario, config, policy, seed_namespace=cohort)
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name=scenario_name,
        scenario=scenario,
        orchestrator=orchestrator,
        supervisor=supervisor,
        limits=EvaluationLimits(
            timeout_seconds=config.timeout_seconds,
            max_memory_mb=config.max_memory_mb,
            network_access=False,
        ),
        hook_bus=hook_bus,
        generation_index=generation_index,
        store=store,
        expected_evaluator_epoch=evaluator_epoch,
    )
    return ContextBundlePromotionCoordinator(
        store,
        evaluator,
        units,
        cohort=cohort,
        policy=policy,
        lifecycle_auditor=lifecycle_auditor,
        false_promotion_controller=CampaignFalsePromotionController(
            risk_root,
            config.false_promotion_policy(),
        ),
        campaign_id=run_id,
    )


def _evaluation_units(
    scenario: object,
    config: LiveContextPromotionConfig,
    policy: ConfirmationPolicy,
    *,
    seed_namespace: str,
) -> tuple[ContextBundleEvaluationUnit, ...]:
    from autocontext.scenarios.base import ScenarioInterface

    if not isinstance(scenario, ScenarioInterface):
        raise TypeError("live context promotion requires a ScenarioInterface")
    if not seed_namespace:
        raise ValueError("live context promotion seed namespace must be non-empty")
    specs = (
        (TrialLane.SCREEN, policy.min_screen_pairs),
        (TrialLane.CONFIRMATION, policy.max_confirmation_pairs),
        (TrialLane.HELDOUT, policy.min_heldout_pairs),
    )
    total_units = sum(count for _, count in specs)
    if (
        isinstance(config.seed_base, bool)
        or not isinstance(config.seed_base, int)
        or not 0 <= config.seed_base <= MAX_SAFE_INTEGER
    ):
        raise ValueError("live context promotion seed_base must be a non-negative safe integer")
    available = MAX_SAFE_INTEGER - config.seed_base + 1
    namespace_slots = available // total_units
    if namespace_slots < 1:
        raise ValueError("live context promotion seed plan exceeds the safe integer range")
    namespace_index = (
        int(
            stable_digest({"runtime_context_seed_namespace": seed_namespace}),
            16,
        )
        % namespace_slots
    )
    seed_start = config.seed_base + namespace_index * total_units
    units: list[ContextBundleEvaluationUnit] = []
    seed_offset = 0
    for lane, count in specs:
        for index in range(count):
            seed = seed_start + seed_offset + index
            try:
                fixture_scenario = copy.deepcopy(scenario)
            except Exception as exc:
                raise RuntimeError("live context promotion requires an isolated scenario for fixture planning") from exc
            materialized = materialize_runtime_fixture(fixture_scenario, seed)
            # Independence is a property of the actual evaluation fixture, not
            # of the seed or lane label used to request it. A scenario that
            # ignores its seed must therefore collapse to one dependence block,
            # and the same fixture reused across lanes must fail the disjointness
            # gate rather than acquiring a fresh digest from relabeling.
            fixture_digest = materialized.digest
            fixture = f"runtime-fixture-{fixture_digest[:16]}"
            units.append(ContextBundleEvaluationUnit(fixture, fixture_digest, seed, lane))
        seed_offset += count
    return tuple(units)


__all__ = ["LiveContextPromotionConfig", "build_live_context_promotion"]
