from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autocontext.agents.agent_sdk_client import AgentSdkClient
from autocontext.agents.context_evaluation_isolation import isolate_context_bundle_client
from autocontext.agents.llm_client import AnthropicClient, DeterministicDevClient, LanguageModelClient
from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.agents.panel_runtime import PanelConfig, PanelLanguageModelClient, PanelParticipant
from autocontext.agents.provider_bridge import ProviderBridgeClient, RuntimeBridgeClient
from autocontext.config.settings import AppSettings
from autocontext.context_bundles import (
    BundleComponent,
    BundleLifecycle,
    CampaignFalsePromotionController,
    CampaignFalsePromotionPolicy,
    ComparisonDecision,
    ComponentKind,
    ConfirmationPolicy,
    ContextBundle,
    ContextBundleEvaluationOutcome,
    ContextBundleEvaluationUnit,
    ContextBundlePromotionCoordinator,
    ContextBundleStore,
    MatchedTrial,
    TrialLane,
    evaluator_epoch_for,
    stable_digest,
)
from autocontext.context_bundles.false_promotion import required_confidence_z
from autocontext.context_bundles.runtime_evaluator import (
    build_runtime_context_bundle_evaluator,
    materialize_runtime_fixture,
)
from autocontext.execution.executors.local import LocalExecutor
from autocontext.execution.supervisor import ExecutionSupervisor
from autocontext.extensions import HookBus, HookedLanguageModelClient, HookEvents
from autocontext.harness.core.types import ModelResponse, RoleUsage
from autocontext.harness.evaluation.types import EvaluationLimits
from autocontext.loop.context_bundle_evaluation import (
    ContextBundleEvaluationDeferred,
    evaluate_context_candidate,
    resume_pending_context_candidate,
)
from autocontext.loop.live_context_promotion import LiveContextPromotionConfig, _evaluation_units
from autocontext.providers.base import CompletionResult, LLMProvider, OutputSchema
from autocontext.runtimes.base import AgentOutput, AgentRuntime
from autocontext.runtimes.codex_cli import CodexCLIRuntime
from autocontext.scenarios.base import ExecutionLimits, Observation, ReplayEnvelope, Result, ScenarioInterface
from autocontext.storage import ArtifactStore
from autocontext.storage.negative_result_ledger_store import (
    negative_result_ledger_path,
    read_latest_negative_result_ledgers_markdown,
    read_negative_result_ledger,
    write_negative_result_ledger,
)


def _bundle(
    playbook: str,
    *,
    parent: str | None = None,
    epoch: str = "epoch-1",
    model: str = "small",
) -> ContextBundle:
    return ContextBundle.create(
        scenario="demo",
        evaluator_epoch=epoch,
        parent_digest=parent,
        components=[
            BundleComponent(ComponentKind.PLAYBOOK, "playbook", playbook, "text/markdown"),
            BundleComponent.json(ComponentKind.ROUTING_CONFIG, "roles", {"model_competitor": model}),
        ],
    )


def _policy(*, max_confirmation_pairs: int = 2) -> ConfirmationPolicy:
    return ConfirmationPolicy(
        min_screen_pairs=1,
        min_confirmation_pairs=2,
        max_confirmation_pairs=max_confirmation_pairs,
        min_heldout_pairs=1,
    )


def _units(*, third_confirmation: bool = False) -> tuple[ContextBundleEvaluationUnit, ...]:
    confirmation = [
        ContextBundleEvaluationUnit("confirm-a", "confirm-a", 2, TrialLane.CONFIRMATION),
        ContextBundleEvaluationUnit("confirm-b", "confirm-b", 3, TrialLane.CONFIRMATION),
    ]
    if third_confirmation:
        confirmation.append(ContextBundleEvaluationUnit("confirm-c", "confirm-c", 4, TrialLane.CONFIRMATION))
    return (
        ContextBundleEvaluationUnit("screen", "screen", 1, TrialLane.SCREEN),
        *confirmation,
        ContextBundleEvaluationUnit("heldout", "heldout", 5, TrialLane.HELDOUT),
    )


class _WinningEvaluator:
    def __init__(self, candidate_digest: str) -> None:
        self.candidate_digest = candidate_digest
        self.calls: list[tuple[str, str]] = []

    def evaluate(
        self,
        bundle: ContextBundle,
        unit: ContextBundleEvaluationUnit,
    ) -> ContextBundleEvaluationOutcome:
        self.calls.append((bundle.digest, unit.fixture_digest))
        return ContextBundleEvaluationOutcome(score=0.8 if bundle.digest == self.candidate_digest else 0.5)

    def evaluation_plan_identity(self, bundle: ContextBundle) -> Mapping[str, Any]:
        return {
            "implementation": "test-winning-evaluator-v1",
            "candidate_digest": self.candidate_digest,
            "bundle_digest": bundle.digest,
        }


def _confirmed_store(tmp_path: Path) -> tuple[ContextBundleStore, ContextBundle, ContextBundle, ConfirmationPolicy]:
    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)
    policy = _policy()
    trials = [
        MatchedTrial(
            candidate_digest=candidate.digest,
            incumbent_digest=incumbent.digest,
            evaluator_epoch=candidate.evaluator_epoch,
            cohort="cohort",
            fixture=unit.fixture,
            fixture_digest=unit.fixture_digest,
            seed=unit.seed,
            lane=unit.lane,
            candidate_score=0.8,
            incumbent_score=0.5,
        )
        for unit in _units()
    ]
    comparison = store.record_matched_trials("demo", candidate.digest, trials, policy=policy)
    assert comparison.decision == ComparisonDecision.CONFIRMED
    return store, incumbent, candidate, policy


def test_cli_imports_in_a_clean_process_without_package_cycles() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import autocontext.cli"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_evaluator_epoch_rollover_reanchors_unchanged_context(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    previous = store.bootstrap(_bundle("stable context", epoch="epoch-1"))

    current = store.rollover_evaluator_epoch("demo", "epoch-2")

    assert current.digest != previous.digest
    assert current.parent_digest is None
    assert current.evaluator_epoch == "epoch-2"
    assert current.components == previous.components
    assert store.active_bundle("demo") == current
    assert store.candidate("demo", previous.digest).lifecycle == BundleLifecycle.SUPERSEDED
    assert store.candidate("demo", current.digest).lifecycle == BundleLifecycle.ACTIVE


def test_partial_matched_evidence_resumes_without_replaying_committed_pairs(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)
    assert [record.bundle_digest for record in store.pending_candidates("demo", "run", 1)] == [candidate.digest]

    class _FailAfterScreen(_WinningEvaluator):
        def evaluate(
            self,
            bundle: ContextBundle,
            unit: ContextBundleEvaluationUnit,
        ) -> ContextBundleEvaluationOutcome:
            if unit.lane == TrialLane.CONFIRMATION:
                raise RuntimeError("interrupted confirmation")
            return super().evaluate(bundle, unit)

    first = _FailAfterScreen(candidate.digest)
    coordinator = ContextBundlePromotionCoordinator(
        store,
        first,
        _units(),
        cohort="cohort",
        policy=_policy(),
    )
    with pytest.raises(RuntimeError, match="interrupted confirmation"):
        coordinator.evaluate_candidate("demo", candidate.digest)
    assert len(store.matched_trials("demo", candidate.digest)) == 1
    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.SCREENED
    assert [record.bundle_digest for record in store.pending_candidates("demo", "run", 1)] == [candidate.digest]
    assert store.pending_candidates("demo", "another-run", 1) == ()
    assert store.pending_candidates("demo", "run", 2) == ()

    resumed = _WinningEvaluator(candidate.digest)
    result = ContextBundlePromotionCoordinator(
        store,
        resumed,
        _units(),
        cohort="cohort",
        policy=_policy(),
    ).evaluate_candidate("demo", candidate.digest)

    assert result.promotion is not None
    assert result.evaluated_pairs == 3
    assert all(fixture != "screen" for _, fixture in resumed.calls)
    assert store.pending_candidates("demo", "run", 1) == ()


def test_audit_hold_retries_confirmed_evidence_without_new_evaluation(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)
    evaluator = _WinningEvaluator(candidate.digest)

    class _Audit:
        def __init__(self) -> None:
            self.outcome = "review_required"
            self.calls = 0

        def review_pre_promotion(
            self,
            candidate: ContextBundle,
            comparison: Any,
            trials: tuple[MatchedTrial, ...],
            *,
            cancellation_event: Any = None,
        ) -> str:
            del candidate, comparison, trials, cancellation_event
            self.calls += 1
            return self.outcome

    audit = _Audit()
    coordinator = ContextBundlePromotionCoordinator(
        store,
        evaluator,
        _units(),
        cohort="cohort",
        policy=_policy(),
        audit_checkpoint=audit,
    )
    held = coordinator.evaluate_candidate("demo", candidate.digest)
    call_count = len(evaluator.calls)
    assert held.promotion is None
    assert held.audit_policy_outcome == "review_required"
    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.CONFIRMED

    audit.outcome = "advisory"
    resumed = coordinator.evaluate_candidate("demo", candidate.digest)

    assert resumed.promotion is not None
    assert resumed.evaluated_pairs == 0
    assert len(evaluator.calls) == call_count
    assert audit.calls == 2


def test_enabled_lifecycle_audit_failure_holds_and_retries_without_new_evidence(
    tmp_path: Path,
) -> None:
    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)
    evaluator = _WinningEvaluator(candidate.digest)

    class _FlakyLifecycleAudit:
        calls = 0

        def review_checkpoint(
            self,
            checkpoint: str,
            evidence: Mapping[str, Any],
            *,
            cancellation_event: Any = None,
        ) -> Any:
            del checkpoint, evidence, cancellation_event
            self.calls += 1
            if self.calls == 1:
                raise OSError("audit transport unavailable")
            return SimpleNamespace(status="completed", policy_outcome="advisory")

    audit = _FlakyLifecycleAudit()
    coordinator = ContextBundlePromotionCoordinator(
        store,
        evaluator,
        _units(),
        cohort="cohort",
        policy=_policy(),
        lifecycle_auditor=audit,
    )

    held = coordinator.evaluate_candidate("demo", candidate.digest)
    evaluated_calls = len(evaluator.calls)
    assert [record.bundle_digest for record in store.pending_candidates("demo", "run", 1)] == [candidate.digest]
    resumed = coordinator.evaluate_candidate("demo", candidate.digest)

    assert held.promotion is None
    assert held.audit_policy_outcome == "safe_pause_recommended"
    assert resumed.promotion is not None
    assert resumed.evaluated_pairs == 0
    assert len(evaluator.calls) == evaluated_calls
    assert audit.calls == 2
    assert store.pending_candidates("demo", "run", 1) == ()


def test_pending_candidate_query_fails_on_corrupt_record_identity(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)
    path = store._record_path("demo", candidate.digest)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["bundle_digest"] = "f" * 64
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ValueError, match="path does not match"):
        store.pending_candidates("demo", "run", 1)


def test_candidate_digest_cannot_be_rebound_across_source_generations(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("same immutable candidate", parent=incumbent.digest)
    first = store.propose(candidate, source_run_id="run-one", source_generation=1)

    with pytest.raises(ValueError, match="different source generation"):
        store.propose(candidate, source_run_id="run-one", source_generation=2)

    rejected = store.reject("demo", candidate.digest, rationale="failed screen")
    with pytest.raises(ValueError, match="different source generation"):
        store.propose(candidate, source_run_id="run-two", source_generation=1)

    assert store.propose(candidate, source_run_id="run-one", source_generation=1) == rejected
    assert first.bundle_digest == rejected.bundle_digest


def test_campaign_gate_collects_additional_independent_blocks_before_heldout(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path / "bundles")
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)
    evaluator = _WinningEvaluator(candidate.digest)
    units = (
        ContextBundleEvaluationUnit("screen", "screen", 1, TrialLane.SCREEN),
        ContextBundleEvaluationUnit("confirm-a", "shared", 2, TrialLane.CONFIRMATION),
        ContextBundleEvaluationUnit("confirm-b", "shared", 3, TrialLane.CONFIRMATION),
        ContextBundleEvaluationUnit("confirm-c", "independent", 4, TrialLane.CONFIRMATION),
        ContextBundleEvaluationUnit("heldout", "heldout", 5, TrialLane.HELDOUT),
    )
    result = ContextBundlePromotionCoordinator(
        store,
        evaluator,
        units,
        cohort="cohort",
        policy=_policy(max_confirmation_pairs=3),
        false_promotion_controller=CampaignFalsePromotionController(tmp_path / "risk"),
        campaign_id="campaign",
    ).evaluate_candidate("demo", candidate.digest)

    assert result.promotion is not None
    assert result.false_promotion_result is not None
    assert result.false_promotion_result.authorized is True
    assert result.false_promotion_result.reservation.independent_confirmation_blocks == 2
    assert any(fixture == "independent" for _, fixture in evaluator.calls)


def test_pre_manifest_diff_candidate_is_migrated_during_promotion(tmp_path: Path) -> None:
    store, _incumbent, candidate, _policy_value = _confirmed_store(tmp_path)
    manifest_path = store._manifest_diff_path("demo", candidate.digest)
    manifest_path.unlink()

    promotion = store.promote("demo", candidate.digest, cohort="cohort", rationale="confirmed")

    assert promotion.manifest_diff_digest
    assert manifest_path.exists()


def test_pointer_commit_is_recoverable_when_lifecycle_mirrors_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, incumbent, candidate, _policy_value = _confirmed_store(tmp_path)
    original = store._set_lifecycle

    def _fail_lifecycle(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("lifecycle mirror unavailable")

    monkeypatch.setattr(store, "_set_lifecycle", _fail_lifecycle)
    committed = store.promote("demo", candidate.digest, cohort="cohort", rationale="confirmed")
    assert store.active_bundle("demo") == candidate
    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.CONFIRMED

    monkeypatch.setattr(store, "_set_lifecycle", original)
    recovered = store.promote("demo", candidate.digest, cohort="cohort", rationale="confirmed")

    assert recovered == committed
    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.ACTIVE
    assert store.candidate("demo", incumbent.digest).lifecycle == BundleLifecycle.SUPERSEDED


def test_negative_result_is_discoverable_and_binds_exact_evidence(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)

    class _LosingEvaluator:
        def evaluate(
            self,
            bundle: ContextBundle,
            unit: ContextBundleEvaluationUnit,
        ) -> ContextBundleEvaluationOutcome:
            del unit
            return ContextBundleEvaluationOutcome(score=0.2 if bundle.digest == candidate.digest else 0.5)

    result = ContextBundlePromotionCoordinator(
        store,
        _LosingEvaluator(),
        _units(),
        cohort="cohort",
        policy=_policy(),
    ).evaluate_candidate("demo", candidate.digest)
    assert result.comparison.decision == ComparisonDecision.REJECTED

    canonical = tmp_path / "demo" / "negative_result_ledgers" / f"context-bundle-{candidate.digest}.json"
    assert canonical.exists()
    ledger = json.loads(canonical.read_text(encoding="utf-8"))
    reference = ledger["entries"][0]["evidence_refs"][0]
    uri, digest, policy_digest = store.matched_evidence_binding("demo", candidate.digest)
    assert reference["uri"] == f"{uri}#sha256={digest}"
    assert (tmp_path / uri).exists()
    assert policy_digest in reference["summary"]
    assert "candidate failed the cheap matched screen" in read_latest_negative_result_ledgers_markdown(
        tmp_path,
        "demo",
    )


def test_false_promotion_block_is_terminal_and_bound_to_durable_evidence(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundles"
    store = ContextBundleStore(bundle_root)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)
    evaluator = _WinningEvaluator(candidate.digest)
    controller = CampaignFalsePromotionController(
        tmp_path / "risk",
        CampaignFalsePromotionPolicy(robust_method="bounded_hoeffding"),
    )
    coordinator = ContextBundlePromotionCoordinator(
        store,
        evaluator,
        _units(),
        cohort="generation:1",
        policy=_policy(),
        false_promotion_controller=controller,
        campaign_id="team/run",
    )

    result = coordinator.evaluate_candidate("demo", candidate.digest)

    assert result.comparison.decision == ComparisonDecision.CONFIRMED
    assert result.promotion is None
    assert result.false_promotion_result is not None
    assert result.false_promotion_result.authorized is False
    reservation = result.false_promotion_result.reservation
    record = store.candidate("demo", candidate.digest)
    assert record.lifecycle == BundleLifecycle.REJECTED
    assert record.comparison == result.comparison.to_dict()
    assert reservation.evidence_digest in record.rationale
    assert store.pending_candidates("demo", "run", 1) == ()
    assert store.active_bundle("demo") == incumbent

    ledger_path = bundle_root / "demo" / "negative_result_ledgers" / f"context-bundle-{candidate.digest}.json"
    first_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry = first_ledger["entries"][0]
    reservation_digest = stable_digest(reservation.to_dict())
    assert entry["reason"] == result.false_promotion_result.reason
    artifact_uri, artifact_fragment = entry["evidence_refs"][1]["uri"].split("#sha256=", 1)
    artifact_path = Path(artifact_uri)
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact_payload = {key: value for key, value in artifact.items() if key != "artifact_digest"}
    assert artifact_fragment == artifact["artifact_digest"] == stable_digest(artifact_payload)
    assert artifact["campaign_id"] == "team/run"
    assert artifact["risk_reservation"] == reservation.to_dict()
    assert "/team/run/" not in artifact_path.as_posix()
    assert reservation_digest in entry["evidence_refs"][1]["summary"]
    assert reservation.evidence_digest in entry["evidence_refs"][1]["summary"]

    evaluated_calls = len(evaluator.calls)
    replayed = coordinator.evaluate_candidate("demo", candidate.digest)
    assert replayed.false_promotion_result is not None
    assert replayed.false_promotion_result.authorized is False
    assert len(evaluator.calls) == evaluated_calls
    assert json.loads(ledger_path.read_text(encoding="utf-8")) == first_ledger


def test_promotion_boundary_refreshes_routing_for_the_live_orchestrator() -> None:
    settings = AppSettings(agent_provider="deterministic", model_competitor="before")
    orchestrator = AgentOrchestrator(_ResettableStatefulClient(), settings)
    active = _bundle("active", model="after")
    result = SimpleNamespace(
        promotion=SimpleNamespace(promotion_id="promotion"),
        comparison=SimpleNamespace(decision=ComparisonDecision.CONFIRMED),
        evaluated_pairs=4,
        audit_policy_outcome=None,
        false_promotion_result=None,
    )
    coordinator = SimpleNamespace(
        evaluate_candidate=lambda _scenario, _digest: result,
        store=SimpleNamespace(active_bundle=lambda _scenario: active),
    )
    events: list[tuple[str, dict[str, Any]]] = []
    emitter = SimpleNamespace(emit=lambda name, payload: events.append((name, payload)))
    context = SimpleNamespace(
        candidate_context_bundle_digest=active.digest,
        scenario_name="demo",
        run_id="run",
        generation=1,
        active_context_bundle_digest=None,
        active_context_routing={},
        settings=settings,
    )

    evaluate_context_candidate(context, coordinator, orchestrator, emitter)

    assert context.settings.model_competitor == "after"
    assert orchestrator.settings.model_competitor == "after"
    assert orchestrator.competitor.model == "after"
    assert events[-1][1]["promoted"] is True


def test_audit_hold_defers_generation_after_persisting_the_evaluation_event() -> None:
    settings = AppSettings(agent_provider="deterministic")
    orchestrator = AgentOrchestrator(_ResettableStatefulClient(), settings)
    result = SimpleNamespace(
        promotion=None,
        comparison=SimpleNamespace(decision=ComparisonDecision.CONFIRMED),
        evaluated_pairs=4,
        audit_policy_outcome="review_required",
        false_promotion_result=None,
    )
    coordinator = SimpleNamespace(evaluate_candidate=lambda _scenario, _digest: result)
    events: list[tuple[str, dict[str, Any]]] = []
    context = SimpleNamespace(
        candidate_context_bundle_digest="candidate",
        scenario_name="demo",
        run_id="run",
        generation=1,
        active_context_bundle_digest=None,
        active_context_routing={},
        settings=settings,
    )

    with pytest.raises(ContextBundleEvaluationDeferred, match="held for operator review"):
        evaluate_context_candidate(
            context,
            coordinator,
            orchestrator,
            SimpleNamespace(emit=lambda name, payload: events.append((name, payload))),
        )

    assert events[-1][0] == "context_bundle_evaluated"
    assert events[-1][1]["audit_policy_outcome"] == "review_required"


def test_failed_generation_resumes_one_pending_candidate_before_regeneration() -> None:
    settings = AppSettings(agent_provider="deterministic", model_competitor="before")
    orchestrator = AgentOrchestrator(_ResettableStatefulClient(), settings)
    active = _bundle("active", model="after")
    result = SimpleNamespace(
        promotion=SimpleNamespace(promotion_id="promotion"),
        comparison=SimpleNamespace(decision=ComparisonDecision.CONFIRMED),
        evaluated_pairs=0,
        audit_policy_outcome="advisory",
        false_promotion_result=SimpleNamespace(
            authorized=True,
            reason="authorized",
            reservation=SimpleNamespace(allocated_alpha=0.01),
        ),
    )
    record = SimpleNamespace(bundle_digest=active.digest, lifecycle=BundleLifecycle.CONFIRMED)
    store = SimpleNamespace(
        pending_candidates=lambda scenario, run_id, generation: (
            record if (scenario, run_id, generation) == ("demo", "run", 1) else None,
        ),
        active_bundle=lambda _scenario: active,
    )
    coordinator = SimpleNamespace(
        store=store,
        evaluate_candidate=lambda _scenario, _digest: result,
    )
    events: list[tuple[str, dict[str, Any]]] = []
    context = SimpleNamespace(
        candidate_context_bundle_digest=None,
        scenario_name="demo",
        run_id="run",
        generation=1,
        active_context_bundle_digest=None,
        active_context_routing={},
        settings=settings,
    )

    resumed = resume_pending_context_candidate(
        context,
        coordinator,
        orchestrator,
        SimpleNamespace(emit=lambda name, payload: events.append((name, payload))),
    )

    assert resumed is True
    assert context.candidate_context_bundle_digest is None
    assert context.active_context_bundle_digest == active.digest
    assert context.settings.model_competitor == "after"
    assert [name for name, _ in events][-2:] == ["context_bundle_evaluated", "context_bundle_resume_completed"]


class _ResettableStatefulClient(LanguageModelClient):
    context_bundle_evaluation_deadline_enforced = True
    context_bundle_evaluation_cancellation_enforced = True

    def __init__(self) -> None:
        self.history: list[str] = []
        self.competitor_prompts: list[str] = []
        self.competitor_models: list[str] = []
        self.prior_history_lengths: list[int] = []

    def reset_context_for_evaluation(self) -> bool:
        self.history.clear()
        return True

    @contextmanager
    def context_bundle_evaluation_control(
        self,
        *,
        deadline: float,
        cancellation_check: Any,
    ) -> Any:
        if cancellation_check():
            raise RuntimeError("context bundle evaluation was cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("context bundle evaluation arm deadline was exhausted")
        yield
        if cancellation_check():
            raise RuntimeError("context bundle evaluation was cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("context bundle evaluation arm deadline was exhausted")

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        del max_tokens, temperature
        if role == "competitor":
            self.prior_history_lengths.append(len(self.history))
            self.competitor_prompts.append(prompt)
            self.competitor_models.append(model)
        leaked = bool(self.history)
        self.history.append(prompt)
        move = -1 if leaked else (1 if "CANDIDATE_MARKER" in prompt else 0)
        return ModelResponse(
            text=json.dumps({"move": move}),
            usage=RoleUsage(input_tokens=1, output_tokens=1, latency_ms=0, model=model),
        )


class _ScoreScenario(ScenarioInterface):
    name = "demo"

    def __init__(self) -> None:
        self.execution_count = 0

    def describe_rules(self) -> str:
        return "Choose one numeric move."

    def describe_strategy_interface(self) -> str:
        return '{"move": number}'

    def describe_evaluation_criteria(self) -> str:
        return "The score equals move."

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        del seed
        return {}

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        del state, player_id
        return Observation(narrative="Choose now", state={})

    def validate_actions(
        self,
        state: Mapping[str, Any],
        player_id: str,
        actions: Mapping[str, Any],
    ) -> tuple[bool, str]:
        del state, player_id
        return (isinstance(actions.get("move"), (int, float)), "move must be numeric")

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        del state
        return {"move": float(actions["move"]), "terminal": True}

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal"))

    def get_result(self, state: Mapping[str, Any]) -> Result:
        self.execution_count += 1
        score = float(state["move"]) if self.execution_count == 1 else -100.0
        return Result(score=score, winner=None, summary="scored", replay=[{"score": score}])

    def replay_to_narrative(self, replay: Sequence[dict[str, Any]]) -> str:
        return json.dumps(list(replay))

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return dict(state)


class _InlineExecutor:
    context_bundle_evaluation_deadline_enforced = True
    context_bundle_evaluation_cancellation_enforced = True

    @contextmanager
    def context_bundle_evaluation_control(
        self,
        *,
        deadline: float,
        cancellation_check: Any,
    ) -> Any:
        if cancellation_check():
            raise RuntimeError("context bundle evaluation was cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("context bundle evaluation arm deadline was exhausted")
        yield
        if cancellation_check():
            raise RuntimeError("context bundle evaluation was cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("context bundle evaluation arm deadline was exhausted")

    def execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
    ) -> tuple[Result, ReplayEnvelope]:
        del limits
        result = scenario.execute_match(strategy, seed)
        return result, ReplayEnvelope(
            scenario=scenario.name,
            seed=seed,
            narrative=scenario.replay_to_narrative(result.replay),
            timeline=result.replay,
        )

    def execute_prepared_fixture(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
        *,
        initial_state: Mapping[str, Any],
        initial_observation: Observation,
        fixture_digest: str,
    ) -> tuple[Result, ReplayEnvelope]:
        del limits, initial_observation, fixture_digest
        result = scenario.execute_match_from_state(strategy, seed, initial_state)
        return result, ReplayEnvelope(
            scenario=scenario.name,
            seed=seed,
            narrative=scenario.replay_to_narrative(result.replay),
            timeline=result.replay,
        )


def test_live_unit_plan_does_not_relabel_identical_fixtures_as_independent(tmp_path: Path) -> None:
    scenario = _ScoreScenario()  # This scenario deliberately ignores its seed.
    config = LiveContextPromotionConfig(
        min_screen_pairs=1,
        min_confirmation_pairs=2,
        max_confirmation_pairs=3,
        min_heldout_pairs=1,
    )

    units = _evaluation_units(
        scenario,
        config,
        config.confirmation_policy(),
        seed_namespace="run:epoch:generation-1",
    )

    assert len({unit.fixture_digest for unit in units}) == 1
    assert len({unit.fixture for unit in units}) == 1
    lane_digests = {lane: {unit.fixture_digest for unit in units if unit.lane == lane} for lane in TrialLane}
    assert lane_digests[TrialLane.SCREEN] & lane_digests[TrialLane.CONFIRMATION]
    assert lane_digests[TrialLane.CONFIRMATION] & lane_digests[TrialLane.HELDOUT]
    incumbent = _bundle("incumbent")
    candidate = _bundle("candidate", parent=incumbent.digest)
    controller = CampaignFalsePromotionController(tmp_path)
    adjusted, _ = controller.reserve_confirmation_policy(
        "campaign",
        candidate,
        config.confirmation_policy(),
    )
    with pytest.raises(ValueError, match="insufficient independent confirmation fixtures"):
        controller.reserve_fixture_plan(
            "campaign",
            candidate,
            "run:epoch:generation-1",
            tuple((unit.lane, unit.fixture_digest, unit.seed) for unit in units),
            adjusted,
        )


def test_finite_scenario_fixture_reuse_is_rejected_across_adaptive_candidates(
    tmp_path: Path,
) -> None:
    class _FiniteScenario(_ScoreScenario):
        def initial_state(self, seed: int | None = None) -> dict[str, Any]:
            assert seed is not None
            return {"slot": seed % 4}

        def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
            del player_id
            return Observation(narrative=f"slot {state['slot']}", state=dict(state))

    scenario = _FiniteScenario()
    config = LiveContextPromotionConfig(
        min_screen_pairs=1,
        min_confirmation_pairs=2,
        max_confirmation_pairs=2,
        min_heldout_pairs=1,
    )
    policy = config.confirmation_policy()
    first_units = _evaluation_units(scenario, config, policy, seed_namespace="campaign:generation-1")
    second_units = _evaluation_units(scenario, config, policy, seed_namespace="campaign:generation-2")
    assert {unit.seed for unit in first_units}.isdisjoint(unit.seed for unit in second_units)
    assert {unit.fixture_digest for unit in first_units} == {unit.fixture_digest for unit in second_units}

    incumbent = _bundle("incumbent")
    first = _bundle("first", parent=incumbent.digest)
    second = _bundle("second", parent=incumbent.digest)
    controller = CampaignFalsePromotionController(tmp_path)
    first_policy, _ = controller.reserve_confirmation_policy("campaign", first, policy)
    controller.reserve_fixture_plan(
        "campaign",
        first,
        "campaign:generation-1",
        tuple((unit.lane, unit.fixture_digest, unit.seed) for unit in first_units),
        first_policy,
    )
    second_policy, _ = controller.reserve_confirmation_policy("campaign", second, policy)
    with pytest.raises(ValueError, match="reuses actual fixtures"):
        controller.reserve_fixture_plan(
            "campaign",
            second,
            "campaign:generation-2",
            tuple((unit.lane, unit.fixture_digest, unit.seed) for unit in second_units),
            second_policy,
        )


def test_runtime_evaluator_uses_immutable_arm_context_without_identity_or_history_leaks() -> None:
    client = _ResettableStatefulClient()
    settings = AppSettings(agent_provider="deterministic", model_competitor="baseline-model")
    orchestrator = AgentOrchestrator(client, settings)
    scenario = _ScoreScenario()
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=orchestrator,
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )
    incumbent = _bundle("INCUMBENT_MARKER", model="incumbent-model")
    candidate = _bundle(
        "CANDIDATE_MARKER",
        parent=incumbent.digest,
        model="candidate-model",
    )
    unit = ContextBundleEvaluationUnit(
        "secret-fixture-name",
        materialize_runtime_fixture(scenario, 424242).digest,
        424242,
        TrialLane.HELDOUT,
    )

    candidate_outcome = evaluator.evaluate(candidate, unit)
    incumbent_outcome = evaluator.evaluate(incumbent, unit)

    assert candidate_outcome == ContextBundleEvaluationOutcome(score=1.0, valid=True)
    assert incumbent_outcome == ContextBundleEvaluationOutcome(score=0.0, valid=True)
    assert client.prior_history_lengths == [0, 0]
    assert client.competitor_models == ["candidate-model", "incumbent-model"]
    assert "CANDIDATE_MARKER" in client.competitor_prompts[0]
    assert "INCUMBENT_MARKER" in client.competitor_prompts[1]
    assert all(unit.fixture_digest not in prompt for prompt in client.competitor_prompts)
    assert all("secret-fixture-name" not in prompt for prompt in client.competitor_prompts)
    assert all("424242" not in prompt for prompt in client.competitor_prompts)
    assert scenario.execution_count == 0
    assert orchestrator.settings is settings
    assert orchestrator.competitor.model == "baseline-model"


def test_runtime_evaluator_fails_closed_when_scenario_fixture_changes_after_planning() -> None:
    class _ChangingFixtureScenario(_ScoreScenario):
        def __init__(self) -> None:
            super().__init__()
            self.materialization_count = 0

        def initial_state(self, seed: int | None = None) -> dict[str, Any]:
            del seed
            self.materialization_count += 1
            return {"materialization": self.materialization_count}

        def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
            del player_id
            return Observation(narrative="Changing fixture", state=dict(state))

    client = _ResettableStatefulClient()
    orchestrator = AgentOrchestrator(
        client,
        AppSettings(agent_provider="deterministic"),
    )
    scenario = _ChangingFixtureScenario()
    planned = materialize_runtime_fixture(scenario, 7)
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=orchestrator,
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )

    with pytest.raises(RuntimeError, match="predeclared fixture digest"):
        evaluator.evaluate(
            _bundle("candidate"),
            ContextBundleEvaluationUnit("planned", planned.digest, 7, TrialLane.CONFIRMATION),
        )

    assert client.competitor_prompts == []


def test_runtime_evaluator_uses_a_fresh_execution_scenario_after_prompt_materialization() -> None:
    class _CountingScenario(_ScoreScenario):
        def __init__(self) -> None:
            super().__init__()
            self.initial_state_calls = 0

        def initial_state(self, seed: int | None = None) -> dict[str, Any]:
            del seed
            self.initial_state_calls += 1
            return {"initial_state_call": self.initial_state_calls}

        def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
            del player_id
            return Observation(narrative="Counter fixture", state=dict(state))

        def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
            return {
                **dict(state),
                "move": float(actions["move"]),
                "terminal": True,
            }

        def get_result(self, state: Mapping[str, Any]) -> Result:
            score = float(state["move"]) if state["initial_state_call"] == 1 else -100.0
            return Result(score=score, winner=None, summary="scored", replay=[])

    planned = materialize_runtime_fixture(_CountingScenario(), 11)
    scenario = _CountingScenario()
    client = _ResettableStatefulClient()
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=AgentOrchestrator(
            client,
            AppSettings(agent_provider="deterministic"),
        ),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )

    outcome = evaluator.evaluate(
        _bundle("CANDIDATE_MARKER"),
        ContextBundleEvaluationUnit("counter", planned.digest, 11, TrialLane.CONFIRMATION),
    )

    assert outcome == ContextBundleEvaluationOutcome(score=1.0, valid=True)
    assert scenario.initial_state_calls == 0


def test_runtime_evaluator_scores_the_exact_prompt_fixture_for_nondeterministic_scenarios() -> None:
    class _NondeterministicScenario(_ScoreScenario):
        initial_state_calls = 0

        def __init__(self, fixture_value: int) -> None:
            super().__init__()
            self.fixture_value = fixture_value

        def initial_state(self, seed: int | None = None) -> dict[str, Any]:
            del seed
            if type(self).initial_state_calls:
                raise AssertionError("execution attempted to rematerialize a different fixture")
            type(self).initial_state_calls += 1
            return {"fixture_value": self.fixture_value}

        def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
            del player_id
            return Observation(
                narrative=f"fixture value {state['fixture_value']}",
                state=dict(state),
            )

        def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
            return {**dict(state), "move": float(actions["move"]), "terminal": True}

        def get_result(self, state: Mapping[str, Any]) -> Result:
            score = float(state["fixture_value"]) + float(state["move"])
            return Result(score=score, winner=None, summary="scored exact fixture", replay=[])

    planned = materialize_runtime_fixture(_NondeterministicScenario(7), 23)
    _NondeterministicScenario.initial_state_calls = 0
    scenarios = iter((_NondeterministicScenario(7), _NondeterministicScenario(7)))
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=_NondeterministicScenario(7),
        scenario_factory=lambda: next(scenarios),
        orchestrator=AgentOrchestrator(
            _ResettableStatefulClient(),
            AppSettings(agent_provider="deterministic"),
        ),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )

    outcome = evaluator.evaluate(
        _bundle("CANDIDATE_MARKER"),
        ContextBundleEvaluationUnit(
            "nondeterministic",
            planned.digest,
            23,
            TrialLane.CONFIRMATION,
        ),
    )

    assert outcome == ContextBundleEvaluationOutcome(score=8.0, valid=True)


def test_required_confidence_z_handles_extreme_campaign_allocations_conservatively() -> None:
    allocations = [0.08 * 0.5 * 0.5**candidate_index for candidate_index in (47, 48, 100)]
    allocations.append(math.nextafter(0.0, 1.0))
    for alpha in allocations:
        threshold = required_confidence_z(alpha)
        assert math.isfinite(threshold)
        assert math.erfc(threshold / math.sqrt(2.0)) <= alpha


def test_campaign_identity_with_path_characters_is_durably_encoded(tmp_path: Path) -> None:
    incumbent = _bundle("incumbent")
    candidate = _bundle("candidate", parent=incumbent.digest)
    controller = CampaignFalsePromotionController(tmp_path)

    _adjusted, reservation = controller.reserve_confirmation_policy(
        "team/run",
        candidate,
        _policy(),
    )

    assert controller.reservations("team/run") == (reservation,)
    directories = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(directories) == 1
    assert directories[0].name.startswith("%")
    state = json.loads((directories[0] / "false-promotion.json").read_text(encoding="utf-8"))
    assert state["campaign_id"] == "team/run"


def test_campaign_fixture_reservations_survive_restart_and_block_cross_candidate_reuse(
    tmp_path: Path,
) -> None:
    controller = CampaignFalsePromotionController(tmp_path)
    incumbent = _bundle("incumbent")
    first = _bundle("candidate-one", parent=incumbent.digest)
    second = _bundle("candidate-two", parent=incumbent.digest)
    plan = (
        (TrialLane.SCREEN, "screen-a", 1),
        (TrialLane.CONFIRMATION, "confirmation-a", 2),
        (TrialLane.CONFIRMATION, "confirmation-b", 3),
        (TrialLane.HELDOUT, "heldout-a", 4),
    )
    first_policy, _ = controller.reserve_confirmation_policy("campaign", first, _policy())
    reserved = controller.reserve_fixture_plan(
        "campaign",
        first,
        "campaign:epoch:generation-1",
        plan,
        first_policy,
    )

    restarted = CampaignFalsePromotionController(tmp_path)
    assert restarted.fixture_reservations("campaign") == (reserved,)
    assert (
        restarted.reserve_fixture_plan(
            "campaign",
            first,
            "campaign:epoch:generation-1",
            plan,
            first_policy,
        )
        == reserved
    )

    second_policy, _ = restarted.reserve_confirmation_policy("campaign", second, _policy())
    with pytest.raises(ValueError, match="reuses actual fixtures"):
        restarted.reserve_fixture_plan(
            "campaign",
            second,
            "campaign:epoch:generation-2",
            plan,
            second_policy,
        )


def test_legacy_campaign_with_unknown_fixture_history_fails_closed(tmp_path: Path) -> None:
    controller = CampaignFalsePromotionController(tmp_path)
    incumbent = _bundle("incumbent")
    candidate = _bundle("candidate", parent=incumbent.digest)
    adjusted, reservation = controller.reserve_confirmation_policy("legacy", candidate, _policy())
    legacy_payload = {
        "schema_version": 1,
        "campaign_id": "legacy",
        "policy": controller.policy.to_dict(),
        "reservations": [reservation.to_dict()],
    }
    legacy_path = tmp_path / "legacy" / "false-promotion.json"
    legacy_path.write_text(
        json.dumps({**legacy_payload, "state_digest": stable_digest(legacy_payload)}),
        encoding="utf-8",
    )

    restarted = CampaignFalsePromotionController(tmp_path)
    assert restarted.reservations("legacy") == (reservation,)
    with pytest.raises(ValueError, match="no complete fixture history"):
        restarted.reserve_fixture_plan(
            "legacy",
            candidate,
            "legacy:epoch:generation-1",
            (
                (TrialLane.SCREEN, "screen", 1),
                (TrialLane.CONFIRMATION, "confirmation-a", 2),
                (TrialLane.CONFIRMATION, "confirmation-b", 3),
                (TrialLane.HELDOUT, "heldout", 4),
            ),
            adjusted,
        )


def test_negative_ledger_run_identity_cannot_escape_storage_root(tmp_path: Path) -> None:
    from autocontext.analytics.negative_result_ledger import build_negative_result_ledger

    run_id = "../outside/team\\run"
    ledger = build_negative_result_ledger(
        run_id=run_id,
        events=[],
        generated_at="2026-08-20T00:00:00Z",
        scenario_name="demo",
        context_bundle_digest="sha256:bundle",
        evaluator_epoch="epoch-1",
    )

    path = write_negative_result_ledger(tmp_path, "demo", run_id, ledger)

    assert path == negative_result_ledger_path(tmp_path, "demo", run_id)
    assert path.parent == tmp_path / "demo" / "negative_result_ledgers"
    assert path.name.startswith("%")
    assert "/" not in path.name and "\\" not in path.name
    assert read_negative_result_ledger(tmp_path, "demo", run_id) == ledger
    assert ledger.run_id == run_id
    assert not (tmp_path / "outside").exists()


class _UnmarkedStatefulRuntime(AgentRuntime):
    def __init__(self) -> None:
        self.history: list[str] = []

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> AgentOutput:
        del system, schema
        self.history.append(prompt)
        return AgentOutput(text='{"move": 1}')

    def revise(
        self,
        prompt: str,
        previous_output: str,
        feedback: str,
        system: str | None = None,
    ) -> AgentOutput:
        del previous_output, feedback, system
        return self.generate(prompt)


def test_runtime_evaluator_fails_closed_for_unmarked_stateful_runtime() -> None:
    runtime = _UnmarkedStatefulRuntime()
    scenario = _ScoreScenario()
    orchestrator = AgentOrchestrator(
        RuntimeBridgeClient(runtime),
        AppSettings(agent_provider="deterministic"),
    )
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=orchestrator,
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )

    with pytest.raises(RuntimeError, match="cannot prove context-bundle arm isolation"):
        evaluator.evaluate(
            _bundle("candidate"),
            ContextBundleEvaluationUnit(
                "fixture",
                materialize_runtime_fixture(scenario, 1).digest,
                1,
                TrialLane.SCREEN,
            ),
        )
    assert runtime.history == []


def test_context_arm_isolation_rejects_ambient_tool_runtimes_without_proof() -> None:
    with pytest.raises(RuntimeError, match="tool-capable Agent SDK"):
        isolate_context_bundle_client(AgentSdkClient(), role="competitor")

    # Translator is a known one-shot, tool-free Agent SDK role.
    isolate_context_bundle_client(AgentSdkClient(), role="translator")

    runtime = CodexCLIRuntime()
    with pytest.raises(RuntimeError, match="cannot prove context-bundle arm isolation"):
        isolate_context_bundle_client(RuntimeBridgeClient(runtime), role="competitor")

    runtime.context_bundle_evaluation_arm_isolated = True
    isolate_context_bundle_client(RuntimeBridgeClient(runtime), role="competitor")


def test_context_arm_isolation_rejects_dynamic_panel_routes() -> None:
    panel = PanelLanguageModelClient(
        role="competitor",
        base_client=DeterministicDevClient(),
        config=PanelConfig(
            role="competitor",
            participants=(PanelParticipant(provider="agent_sdk", model="participant"),),
            synthesizer_provider="codex",
            synthesizer_model="synthesizer",
        ),
        client_factory=lambda _provider, _model: AgentSdkClient(),
    )

    with pytest.raises(RuntimeError, match="dynamic model panel"):
        isolate_context_bundle_client(panel, role="competitor")


class _UnmarkedStatefulProvider(LLMProvider):
    def __init__(self) -> None:
        self.history: list[str] = []

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        output_schema: OutputSchema | None = None,
    ) -> CompletionResult:
        del system_prompt, model, temperature, max_tokens, output_schema
        self.history.append(user_prompt)
        return CompletionResult(text='{"move": 1}', model="stateful")

    def default_model(self) -> str:
        return "stateful"


def test_runtime_evaluator_fails_closed_for_unmarked_stateful_provider() -> None:
    provider = _UnmarkedStatefulProvider()
    scenario = _ScoreScenario()
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=AgentOrchestrator(
            ProviderBridgeClient(provider),
            AppSettings(agent_provider="deterministic"),
        ),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )

    with pytest.raises(RuntimeError, match="cannot prove context-bundle arm isolation"):
        evaluator.evaluate(
            _bundle("candidate"),
            ContextBundleEvaluationUnit(
                "fixture",
                materialize_runtime_fixture(scenario, 1).digest,
                1,
                TrialLane.SCREEN,
            ),
        )
    assert provider.history == []


def test_runtime_evaluator_observes_stop_between_competitor_and_translator() -> None:
    stopped = False

    class _StopAfterCompetitor(_ResettableStatefulClient):
        def generate(self, **kwargs: Any) -> ModelResponse:
            nonlocal stopped
            response = super().generate(**kwargs)
            if kwargs.get("role") == "competitor":
                stopped = True
            return response

    client = _StopAfterCompetitor()
    scenario = _ScoreScenario()
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=AgentOrchestrator(client, AppSettings(agent_provider="deterministic")),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )
    unit = ContextBundleEvaluationUnit(
        "fixture",
        materialize_runtime_fixture(scenario, 1).digest,
        1,
        TrialLane.SCREEN,
    )

    with pytest.raises(RuntimeError, match="cancelled"):
        evaluator.evaluate_with_control(
            _bundle("candidate"),
            unit,
            deadline=time.monotonic() + 1.0,
            cancellation_check=lambda: stopped,
        )
    assert len(client.competitor_prompts) == 1
    assert len(client.history) == 1


def test_runtime_evaluator_rejects_unbounded_direct_anthropic_before_model_call() -> None:
    scenario = _ScoreScenario()
    client = AnthropicClient("not-used", max_retries=0)
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=AgentOrchestrator(client, AppSettings(agent_provider="anthropic")),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )
    unit = ContextBundleEvaluationUnit(
        "fixture",
        materialize_runtime_fixture(scenario, 1).digest,
        1,
        TrialLane.SCREEN,
    )

    with patch.object(client, "generate", side_effect=AssertionError("model call must not start")) as generate:
        with pytest.raises(RuntimeError, match="cannot prove transport-enforced"):
            evaluator.evaluate_with_control(
                _bundle("candidate"),
                unit,
                deadline=time.monotonic() + 1.0,
                cancellation_check=lambda: False,
            )
    generate.assert_not_called()


def test_runtime_evaluator_rejects_soft_timeout_local_executor_before_dispatch() -> None:
    class _SlowScenario(_ScoreScenario):
        def execute_match_from_state(
            self,
            strategy: Mapping[str, Any],
            seed: int,
            initial_state: Mapping[str, Any],
        ) -> Result:
            del strategy, seed, initial_state
            time.sleep(0.15)
            raise AssertionError("soft-timeout executor must not be dispatched")

    client = _ResettableStatefulClient()
    scenario = _SlowScenario()
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="cannot prove killable"):
        build_runtime_context_bundle_evaluator(
            scenario_name="demo",
            scenario=scenario,
            orchestrator=AgentOrchestrator(client, AppSettings(agent_provider="deterministic")),
            supervisor=ExecutionSupervisor(LocalExecutor()),
            limits=EvaluationLimits(timeout_seconds=0.01),
        )
    assert time.monotonic() - started < 0.05
    assert client.history == []


def test_runtime_evaluator_fails_closed_for_executable_bundle_validators(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        agent_provider="deterministic",
        harness_validators_enabled=True,
        prevalidation_enabled=True,
    )
    store = ContextBundleStore(tmp_path)
    common = [
        BundleComponent.json(ComponentKind.ROUTING_CONFIG, "roles", {}),
    ]
    incumbent = store.bootstrap(
        ContextBundle.create(
            scenario="demo",
            evaluator_epoch="epoch-1",
            components=[
                *common,
                BundleComponent(ComponentKind.PLAYBOOK, "playbook", "INCUMBENT_MARKER"),
                BundleComponent(
                    ComponentKind.HARNESS_VALIDATOR,
                    "move_gate",
                    "def validate_strategy(strategy, scenario):\n    return (True, [])\n",
                    "text/x-python",
                ),
            ],
        )
    )
    candidate = ContextBundle.create(
        scenario="demo",
        evaluator_epoch="epoch-1",
        parent_digest=incumbent.digest,
        components=[
            *common,
            BundleComponent(ComponentKind.PLAYBOOK, "playbook", "CANDIDATE_MARKER"),
            BundleComponent(
                ComponentKind.HARNESS_VALIDATOR,
                "move_gate",
                "def validate_strategy(strategy, scenario):\n    return (False, ['candidate validator rejected'])\n",
                "text/x-python",
            ),
        ],
    )
    store.propose(candidate, source_run_id="run", source_generation=1)
    scenario = _ScoreScenario()
    client = _ResettableStatefulClient()
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=AgentOrchestrator(client, settings),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
        store=store,
    )
    unit = ContextBundleEvaluationUnit(
        "fixture",
        materialize_runtime_fixture(scenario, 17).digest,
        17,
        TrialLane.SCREEN,
    )

    for bundle in (candidate, incumbent):
        with pytest.raises(RuntimeError, match="serving-equivalent revision semantics"):
            evaluator.evaluate(bundle, unit)
        with pytest.raises(RuntimeError, match="serving-equivalent revision semantics"):
            evaluator.evaluation_plan_identity(bundle)
    assert client.history == []


def test_runtime_evaluator_allows_empty_hook_bus_but_rejects_registered_hooks() -> None:
    scenario = _ScoreScenario()
    client = _ResettableStatefulClient()
    bundle = _bundle("candidate")
    unit = ContextBundleEvaluationUnit(
        "fixture",
        materialize_runtime_fixture(scenario, 1).digest,
        1,
        TrialLane.SCREEN,
    )

    empty_bus = HookBus()
    empty = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=AgentOrchestrator(
            client,
            AppSettings(agent_provider="deterministic"),
            hook_bus=empty_bus,
        ),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
        hook_bus=empty_bus,
    )
    assert empty.evaluation_plan_identity(bundle)["bundle_digest"] == bundle.digest

    hooked_bus = HookBus()
    hooked_bus.on(HookEvents.AFTER_PROVIDER_RESPONSE, lambda _event: None)
    hooked = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=AgentOrchestrator(
            client,
            AppSettings(agent_provider="deterministic"),
            hook_bus=hooked_bus,
        ),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
        hook_bus=hooked_bus,
    )
    with pytest.raises(RuntimeError, match="registered extension hooks"):
        hooked.evaluation_plan_identity(bundle)
    with pytest.raises(RuntimeError, match="registered extension hooks"):
        hooked.evaluate(bundle, unit)

    omitted = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=AgentOrchestrator(
            client,
            AppSettings(agent_provider="deterministic"),
            hook_bus=hooked_bus,
        ),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )
    with pytest.raises(RuntimeError, match="hook buses do not match"):
        omitted.evaluation_plan_identity(bundle)
    with pytest.raises(RuntimeError, match="hook buses do not match"):
        omitted.evaluate(bundle, unit)
    assert client.history == []


def test_runtime_evaluator_finds_a_foreign_hook_bus_beneath_recording_wrappers() -> None:
    class _RecordingWrapper(LanguageModelClient):
        def __init__(self, inner: LanguageModelClient) -> None:
            self.inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        def generate(self, **kwargs: Any) -> ModelResponse:
            return self.inner.generate(**kwargs)

    foreign_bus = HookBus()
    foreign_bus.on(HookEvents.AFTER_PROVIDER_RESPONSE, lambda _event: {"text": '{"move": 99}'})
    wrapped = _RecordingWrapper(
        HookedLanguageModelClient(
            _ResettableStatefulClient(),
            foreign_bus,
        )
    )
    scenario = _ScoreScenario()
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=AgentOrchestrator(wrapped, AppSettings(agent_provider="deterministic")),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )

    with pytest.raises(RuntimeError, match="client graph uses a different hook bus"):
        evaluator.evaluate(
            _bundle("candidate"),
            ContextBundleEvaluationUnit(
                "fixture",
                materialize_runtime_fixture(scenario, 1).digest,
                1,
                TrialLane.SCREEN,
            ),
        )


def test_runtime_evaluator_rejects_a_hooked_client_hidden_in_a_nested_wrapper_graph() -> None:
    class _NestedRecordingWrapper(LanguageModelClient):
        context_bundle_evaluation_deadline_enforced = True
        context_bundle_evaluation_cancellation_enforced = True

        def __init__(self, effective_client: LanguageModelClient) -> None:
            cycle: dict[str, object] = {}
            self.inner: dict[str, object] = {
                "layers": [{"client": effective_client}],
                "cycle": cycle,
            }
            cycle["owner"] = self

        def _effective_client(self) -> LanguageModelClient:
            layers = self.inner["layers"]
            assert isinstance(layers, list)
            entry = layers[0]
            assert isinstance(entry, dict)
            client = entry["client"]
            assert isinstance(client, LanguageModelClient)
            return client

        def reset_context_for_evaluation(self) -> bool:
            reset = self._effective_client().reset_context_for_evaluation  # type: ignore[attr-defined]
            return bool(reset())

        @contextmanager
        def context_bundle_evaluation_control(
            self,
            *,
            deadline: float,
            cancellation_check: Any,
        ) -> Any:
            control = self._effective_client().context_bundle_evaluation_control  # type: ignore[attr-defined]
            with control(deadline=deadline, cancellation_check=cancellation_check):
                yield

        def generate(self, **kwargs: Any) -> ModelResponse:
            return self._effective_client().generate(**kwargs)

    hook_calls: list[str] = []
    foreign_bus = HookBus()
    foreign_bus.on(
        HookEvents.AFTER_PROVIDER_RESPONSE,
        lambda _event: hook_calls.append("called"),
    )
    wrapped = _NestedRecordingWrapper(
        HookedLanguageModelClient(
            _ResettableStatefulClient(),
            foreign_bus,
        )
    )
    scenario = _ScoreScenario()
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=AgentOrchestrator(wrapped, AppSettings(agent_provider="deterministic")),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )
    bundle = _bundle("candidate")
    unit = ContextBundleEvaluationUnit(
        "fixture",
        materialize_runtime_fixture(scenario, 1).digest,
        1,
        TrialLane.SCREEN,
    )

    with pytest.raises(RuntimeError, match="client graph uses a different hook bus"):
        evaluator.evaluation_plan_identity(bundle)
    with pytest.raises(RuntimeError, match="client graph uses a different hook bus"):
        evaluator.evaluate(bundle, unit)
    assert hook_calls == []


def test_runtime_evaluator_hook_graph_scan_tolerates_safe_object_and_container_cycles() -> None:
    class _CyclicRecordingWrapper(LanguageModelClient):
        context_bundle_evaluation_deadline_enforced = True
        context_bundle_evaluation_cancellation_enforced = True

        def __init__(self, effective_client: LanguageModelClient) -> None:
            self.inner = effective_client
            self.client_graph: dict[str, object] = {"clients": [effective_client]}
            self.client_graph["owner"] = self
            self.client_graph["self"] = self.client_graph

        def reset_context_for_evaluation(self) -> bool:
            reset = self.inner.reset_context_for_evaluation  # type: ignore[attr-defined]
            return bool(reset())

        @contextmanager
        def context_bundle_evaluation_control(
            self,
            *,
            deadline: float,
            cancellation_check: Any,
        ) -> Any:
            control = self.inner.context_bundle_evaluation_control  # type: ignore[attr-defined]
            with control(deadline=deadline, cancellation_check=cancellation_check):
                yield

        def generate(self, **kwargs: Any) -> ModelResponse:
            return self.inner.generate(**kwargs)

    scenario = _ScoreScenario()
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=AgentOrchestrator(
            _CyclicRecordingWrapper(_ResettableStatefulClient()),
            AppSettings(agent_provider="deterministic"),
        ),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )
    bundle = _bundle("candidate")

    assert evaluator.evaluation_plan_identity(bundle)["bundle_digest"] == bundle.digest


def test_runtime_evaluator_validates_a_newly_resolved_client_before_resetting_it() -> None:
    class _ResetSideEffectWrapper(LanguageModelClient):
        context_bundle_evaluation_deadline_enforced = True
        context_bundle_evaluation_cancellation_enforced = True

        def __init__(self, effective_client: LanguageModelClient) -> None:
            self.inner = {"clients": [effective_client]}
            self.reset_calls = 0

        def reset_context_for_evaluation(self) -> bool:
            self.reset_calls += 1
            return True

        @contextmanager
        def context_bundle_evaluation_control(self, **_kwargs: Any) -> Any:
            yield

        def generate(self, **kwargs: Any) -> ModelResponse:
            clients = self.inner["clients"]
            assert isinstance(clients, list)
            client = clients[0]
            assert isinstance(client, LanguageModelClient)
            return client.generate(**kwargs)

    foreign_bus = HookBus()
    foreign_bus.on(HookEvents.AFTER_PROVIDER_RESPONSE, lambda _event: None)
    wrapped = _ResetSideEffectWrapper(
        HookedLanguageModelClient(
            _ResettableStatefulClient(),
            foreign_bus,
        )
    )
    safe = _ResettableStatefulClient()
    scenario = _ScoreScenario()
    orchestrator = AgentOrchestrator(safe, AppSettings(agent_provider="deterministic"))
    evaluator = build_runtime_context_bundle_evaluator(
        scenario_name="demo",
        scenario=scenario,
        orchestrator=orchestrator,
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )
    unit = ContextBundleEvaluationUnit(
        "fixture",
        materialize_runtime_fixture(scenario, 1).digest,
        1,
        TrialLane.SCREEN,
    )

    # The first two resolutions are the evaluator's competitor/translator
    # preflight. The third is the effective competitor selected inside the
    # runner scope, where validation must precede even reset/isolation hooks.
    with (
        patch.object(
            orchestrator,
            "_resolve_role_execution",
            side_effect=[(safe, None), (safe, None), (wrapped, None)],
        ),
        pytest.raises(RuntimeError, match="client graph uses a different hook bus"),
    ):
        evaluator.evaluate(_bundle("candidate"), unit)

    assert wrapped.reset_calls == 0


def test_evaluator_epoch_uses_a_stable_contract_for_test_doubles_only() -> None:
    settings = AppSettings(agent_provider="deterministic")
    scenario_double = MagicMock()
    scenario_double.describe_strategy_interface.return_value = "aggression: float"

    assert evaluator_epoch_for(scenario_double, settings) == evaluator_epoch_for(scenario_double, settings)

    real_scenario = _ScoreScenario()
    with patch.object(real_scenario, "describe_rules", return_value=MagicMock()):
        with pytest.raises(TypeError, match=r"describe_rules\(\) must return a string"):
            evaluator_epoch_for(real_scenario, settings)


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("code_strategies_enabled", True),
        ("competitor_max_tokens", 801),
        ("translator_max_tokens", 1025),
        ("constrained_output", False),
        ("panel_roles", "competitor"),
        ("panel_participants", "competitor=deterministic:panel-model"),
        ("panel_synthesizer_provider", "deterministic"),
        ("panel_synthesizer_model", "synthesizer-model"),
    ],
)
def test_evaluator_epoch_and_arm_plan_bind_every_generation_knob(
    field: str,
    changed_value: Any,
) -> None:
    scenario = _ScoreScenario()
    baseline = AppSettings(agent_provider="deterministic")
    changed = baseline.model_copy(update={field: changed_value})
    assert evaluator_epoch_for(scenario, baseline) != evaluator_epoch_for(scenario, changed)

    def identity(settings: AppSettings) -> Mapping[str, Any]:
        return build_runtime_context_bundle_evaluator(
            scenario_name="demo",
            scenario=scenario,
            orchestrator=AgentOrchestrator(DeterministicDevClient(), settings),
            supervisor=ExecutionSupervisor(_InlineExecutor()),
        ).evaluation_plan_identity(_bundle("candidate"))

    assert identity(baseline) != identity(changed)


def test_evaluator_epoch_and_arm_plan_bind_scenario_instance_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "fixtures"))
    from remote_custom_scenario import BiasedScenario

    settings = AppSettings(agent_provider="deterministic")
    low = BiasedScenario(bias=0.1)
    high = BiasedScenario(bias=0.4)
    assert evaluator_epoch_for(low, settings) != evaluator_epoch_for(high, settings)

    def identity(scenario: ScenarioInterface) -> Mapping[str, Any]:
        return build_runtime_context_bundle_evaluator(
            scenario_name="biased",
            scenario=scenario,
            orchestrator=AgentOrchestrator(DeterministicDevClient(), settings),
            supervisor=ExecutionSupervisor(_InlineExecutor()),
        ).evaluation_plan_identity(
            ContextBundle.create(
                scenario="biased",
                evaluator_epoch="epoch-1",
                components=[BundleComponent.json(ComponentKind.ROUTING_CONFIG, "roles", {})],
            )
        )

    assert identity(low) != identity(high)
    mismatched_factory = build_runtime_context_bundle_evaluator(
        scenario_name="biased",
        scenario=low,
        scenario_factory=lambda: BiasedScenario(bias=0.4),
        orchestrator=AgentOrchestrator(DeterministicDevClient(), settings),
        supervisor=ExecutionSupervisor(_InlineExecutor()),
    )
    biased_bundle = ContextBundle.create(
        scenario="biased",
        evaluator_epoch="epoch-1",
        components=[BundleComponent.json(ComponentKind.ROUTING_CONFIG, "roles", {})],
    )
    with pytest.raises(RuntimeError, match="changed the bound scenario instance state"):
        mismatched_factory.evaluate(
            biased_bundle,
            ContextBundleEvaluationUnit(
                "fixture",
                materialize_runtime_fixture(low, 1).digest,
                1,
                TrialLane.SCREEN,
            ),
        )
    low.noncanonical = object()
    with pytest.raises(TypeError, match="not canonical"):
        evaluator_epoch_for(low, settings)
    del low.noncanonical
    low.api_key = "must-not-enter-evidence"
    with pytest.raises(ValueError, match="sensitive field"):
        evaluator_epoch_for(low, settings)


def test_prevalidation_loads_the_active_bundle_harness_without_two_tier_gate(
    tmp_path: Path,
) -> None:
    from autocontext.loop.stage_helpers.context_loaders import _load_validity_harness_loader

    artifacts = ArtifactStore(
        tmp_path / "runs",
        tmp_path / "knowledge",
        tmp_path / "skills",
        tmp_path / ".claude/skills",
    )
    artifacts.write_harness(
        "demo",
        "legacy_allow",
        "def validate_strategy(strategy, scenario):\n    return (True, [])\n",
    )
    active = artifacts.context_bundle_store.bootstrap(
        ContextBundle.create(
            scenario="demo",
            evaluator_epoch="epoch-1",
            components=[
                BundleComponent.json(ComponentKind.ROUTING_CONFIG, "roles", {}),
                BundleComponent(
                    ComponentKind.HARNESS_VALIDATOR,
                    "bundle_reject",
                    "def validate_strategy(strategy, scenario):\n    return (False, ['bundle validator'])\n",
                    "text/x-python",
                ),
            ],
        )
    )
    ctx = SimpleNamespace(
        scenario_name="demo",
        active_context_bundle_digest=active.digest,
        settings=AppSettings(
            agent_provider="deterministic",
            harness_validators_enabled=True,
            prevalidation_enabled=True,
            two_tier_gating_enabled=False,
        ),
    )

    loader = _load_validity_harness_loader(ctx, artifacts=artifacts)

    assert loader is not None
    validation = loader.validate_strategy({"move": 1}, _ScoreScenario())
    assert validation.passed is False
    assert validation.errors == ["[bundle_reject] bundle validator"]

    empty_artifacts = ArtifactStore(
        tmp_path / "empty/runs",
        tmp_path / "empty/knowledge",
        tmp_path / "empty/skills",
        tmp_path / "empty/.claude/skills",
    )
    empty_artifacts.write_harness(
        "demo",
        "legacy_reject",
        "def validate_strategy(strategy, scenario):\n    return (False, ['legacy validator'])\n",
    )
    empty_active = empty_artifacts.context_bundle_store.bootstrap(
        ContextBundle.create(
            scenario="demo",
            evaluator_epoch="epoch-1",
            components=[BundleComponent.json(ComponentKind.ROUTING_CONFIG, "roles", {})],
        )
    )
    empty_ctx = SimpleNamespace(
        scenario_name="demo",
        active_context_bundle_digest=empty_active.digest,
        settings=ctx.settings,
    )

    assert _load_validity_harness_loader(empty_ctx, artifacts=empty_artifacts) is None


def test_evaluator_plan_is_complete_immutable_and_checked_before_resume_calls(
    tmp_path: Path,
) -> None:
    scenario = _ScoreScenario()
    baseline_settings = AppSettings(agent_provider="deterministic")
    assert evaluator_epoch_for(scenario, baseline_settings) != evaluator_epoch_for(
        scenario,
        baseline_settings.model_copy(update={"context_bundle_promotion_eval_timeout_seconds": 11.0}),
    )

    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)

    class _InterruptAfterScreen(_WinningEvaluator):
        def evaluate(
            self,
            bundle: ContextBundle,
            unit: ContextBundleEvaluationUnit,
        ) -> ContextBundleEvaluationOutcome:
            if unit.lane == TrialLane.CONFIRMATION:
                raise RuntimeError("planned interruption")
            return super().evaluate(bundle, unit)

    coordinator = ContextBundlePromotionCoordinator(
        store,
        _InterruptAfterScreen(candidate.digest),
        _units(),
        cohort="cohort",
        policy=_policy(),
    )
    with pytest.raises(RuntimeError, match="planned interruption"):
        coordinator.evaluate_candidate("demo", candidate.digest)
    plan_path = store._candidate_dir("demo", candidate.digest) / "evaluator_plan.json"
    artifact = json.loads(plan_path.read_text(encoding="utf-8"))
    assert artifact["plan"]["confirmation_policy_digest"] == stable_digest(_policy().to_dict())
    artifact["plan"]["cohort"] = "adaptively-changed"
    artifact["plan_digest"] = stable_digest(artifact["plan"])
    plan_path.write_text(json.dumps(artifact), encoding="utf-8")
    resumed = _WinningEvaluator(candidate.digest)

    with pytest.raises(ValueError, match="evaluator plan changed after evaluation began"):
        ContextBundlePromotionCoordinator(
            store,
            resumed,
            _units(),
            cohort="cohort",
            policy=_policy(),
        ).evaluate_candidate("demo", candidate.digest)
    assert resumed.calls == []


def test_evaluator_identity_mutation_stops_before_the_second_arm_or_evidence(
    tmp_path: Path,
) -> None:
    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)

    class _MutatingIdentityEvaluator:
        def __init__(self) -> None:
            self.version = 1
            self.calls: list[str] = []

        def evaluation_plan_identity(self, bundle: ContextBundle) -> Mapping[str, Any]:
            return {"bundle_digest": bundle.digest, "version": self.version}

        def evaluate(
            self,
            bundle: ContextBundle,
            unit: ContextBundleEvaluationUnit,
        ) -> ContextBundleEvaluationOutcome:
            del unit
            self.calls.append(bundle.digest)
            self.version = 2
            return ContextBundleEvaluationOutcome(score=1.0)

    evaluator = _MutatingIdentityEvaluator()
    coordinator = ContextBundlePromotionCoordinator(
        store,
        evaluator,
        _units(),
        cohort="cohort",
        policy=_policy(),
    )

    with pytest.raises(ValueError, match="evaluator identity changed"):
        coordinator.evaluate_candidate("demo", candidate.digest)
    assert evaluator.calls == [candidate.digest]
    assert store.matched_trials("demo", candidate.digest) == []
    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.PROPOSED


def test_context_evaluation_deadline_and_stop_fail_before_another_arm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.context_bundles import evaluation_control

    class _ControlledEvaluator:
        def __init__(self, on_first_call: Any) -> None:
            self.calls: list[tuple[str, str]] = []
            self.on_first_call = on_first_call

        def evaluation_plan_identity(self, bundle: ContextBundle) -> Mapping[str, Any]:
            return {"implementation": "controlled-v1", "bundle_digest": bundle.digest}

        def evaluate(
            self,
            bundle: ContextBundle,
            unit: ContextBundleEvaluationUnit,
        ) -> ContextBundleEvaluationOutcome:
            raise AssertionError("bounded evaluation must use evaluate_with_control")

        def evaluate_with_control(
            self,
            bundle: ContextBundle,
            unit: ContextBundleEvaluationUnit,
            *,
            deadline: float | None,
            cancellation_check: Any,
        ) -> ContextBundleEvaluationOutcome:
            assert deadline is not None or cancellation_check is not None
            self.calls.append((bundle.digest, unit.fixture_digest))
            if len(self.calls) == 1:
                self.on_first_call()
            return ContextBundleEvaluationOutcome(score=1.0 if bundle.parent_digest else 0.0)

    def coordinator(root: Path, evaluator: _ControlledEvaluator) -> tuple[ContextBundlePromotionCoordinator, str]:
        store = ContextBundleStore(root)
        incumbent = store.bootstrap(_bundle("incumbent"))
        candidate = _bundle("candidate", parent=incumbent.digest)
        store.propose(candidate, source_run_id="run", source_generation=1)
        return (
            ContextBundlePromotionCoordinator(store, evaluator, _units(), cohort="cohort", policy=_policy()),
            candidate.digest,
        )

    clock = [0.0]
    monkeypatch.setattr(evaluation_control, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    deadline_evaluator = _ControlledEvaluator(lambda: clock.__setitem__(0, 2.0))
    deadline_coordinator, deadline_digest = coordinator(tmp_path / "deadline", deadline_evaluator)
    with pytest.raises(TimeoutError, match="deadline"):
        deadline_coordinator.evaluate_candidate(
            "demo",
            deadline_digest,
            deadline=1.0,
        )
    assert len(deadline_evaluator.calls) == 1

    stopped = False

    def request_stop() -> None:
        nonlocal stopped
        stopped = True

    stop_evaluator = _ControlledEvaluator(request_stop)
    stop_coordinator, stop_digest = coordinator(tmp_path / "stop", stop_evaluator)
    with pytest.raises(RuntimeError, match="cancelled"):
        stop_coordinator.evaluate_candidate(
            "demo",
            stop_digest,
            cancellation_check=lambda: stopped,
        )
    assert len(stop_evaluator.calls) == 1


def test_context_evaluation_rejects_an_already_exhausted_generation_budget(
    tmp_path: Path,
) -> None:
    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)
    evaluator = _WinningEvaluator(candidate.digest)
    coordinator = ContextBundlePromotionCoordinator(
        store,
        evaluator,
        _units(),
        cohort="cohort",
        policy=_policy(),
    )
    settings = AppSettings(agent_provider="deterministic", generation_time_budget_seconds=1)
    ctx = SimpleNamespace(
        candidate_context_bundle_digest=candidate.digest,
        scenario_name="demo",
        run_id="run",
        generation=1,
        settings=settings,
        generation_start_time=time.monotonic() - 2.0,
    )

    with pytest.raises(ContextBundleEvaluationDeferred, match="deadline was exhausted"):
        evaluate_context_candidate(
            ctx,
            coordinator,
            AgentOrchestrator(DeterministicDevClient(), settings),
            SimpleNamespace(emit=lambda *_args: None),
        )
    assert evaluator.calls == []


def test_stale_confirmed_sibling_terminalizes_with_risk_and_negative_evidence(
    tmp_path: Path,
) -> None:
    store = ContextBundleStore(tmp_path / "bundles")
    incumbent = store.bootstrap(_bundle("incumbent"))
    first = _bundle("first candidate", parent=incumbent.digest)
    second = _bundle("second candidate", parent=incumbent.digest)
    store.propose(first, source_run_id="run", source_generation=1)
    store.propose(second, source_run_id="run", source_generation=1)
    controller = CampaignFalsePromotionController(tmp_path / "risk")

    class _HoldAudit:
        outcome = "review_required"

        def review_pre_promotion(
            self,
            candidate: ContextBundle,
            comparison: Any,
            trials: tuple[MatchedTrial, ...],
            *,
            cancellation_event: Any = None,
        ) -> str:
            del candidate, comparison, trials, cancellation_event
            return self.outcome

    def _candidate_units(prefix: str, seed_offset: int) -> tuple[ContextBundleEvaluationUnit, ...]:
        return tuple(
            ContextBundleEvaluationUnit(
                f"{prefix}-{unit.fixture}",
                f"{prefix}-{unit.fixture_digest}",
                unit.seed + seed_offset,
                unit.lane,
            )
            for unit in _units()
        )

    audit = _HoldAudit()
    second_evaluator = _WinningEvaluator(second.digest)
    second_coordinator = ContextBundlePromotionCoordinator(
        store,
        second_evaluator,
        _candidate_units("second", 100),
        cohort="generation:second",
        policy=_policy(),
        audit_checkpoint=audit,
        false_promotion_controller=controller,
        campaign_id="campaign",
    )
    held = second_coordinator.evaluate_candidate("demo", second.digest)
    assert held.audit_policy_outcome == "review_required"
    assert store.candidate("demo", second.digest).lifecycle == BundleLifecycle.CONFIRMED
    evaluated_calls = len(second_evaluator.calls)

    promoted = ContextBundlePromotionCoordinator(
        store,
        _WinningEvaluator(first.digest),
        _candidate_units("first", 0),
        cohort="generation:first",
        policy=_policy(),
        false_promotion_controller=controller,
        campaign_id="campaign",
    ).evaluate_candidate("demo", first.digest)
    assert promoted.promotion is not None
    audit.outcome = "advisory"

    with patch(
        "autocontext.context_bundles.risk_terminalization.reject_stale_risk_reservation",
        side_effect=RuntimeError("crash after durable stale marker"),
    ):
        with pytest.raises(RuntimeError, match="crash after durable stale marker"):
            second_coordinator.evaluate_candidate("demo", second.digest)
    assert store.candidate("demo", second.digest).lifecycle == BundleLifecycle.CONFIRMED
    assert store.pending_candidates("demo", "run", 1)[0].bundle_digest == second.digest

    with patch.object(
        second_coordinator,
        "_persist_negative_result",
        side_effect=RuntimeError("crash after risk disposition"),
    ):
        with pytest.raises(RuntimeError, match="crash after risk disposition"):
            second_coordinator.evaluate_candidate("demo", second.digest)
    reservation = next(item for item in controller.reservations("campaign") if item.candidate_digest == second.digest)
    assert reservation.status == "rejected"
    assert store.candidate("demo", second.digest).lifecycle == BundleLifecycle.CONFIRMED

    original_persist = second_coordinator._persist_negative_result

    def persist_then_rollback(*args: Any, **kwargs: Any) -> None:
        original_persist(*args, **kwargs)
        store.rollback("demo", rationale="concurrent rollback during stale terminalization")

    with patch.object(second_coordinator, "_persist_negative_result", side_effect=persist_then_rollback):
        stale = second_coordinator.evaluate_candidate("demo", second.digest)
    replayed_stale = second_coordinator.evaluate_candidate("demo", second.digest)

    assert stale.promotion is None
    assert replayed_stale.promotion is None
    assert len(second_evaluator.calls) == evaluated_calls
    assert store.candidate("demo", second.digest).lifecycle == BundleLifecycle.REJECTED
    assert store.pending_candidates("demo", "run", 1) == ()
    reservation = next(item for item in controller.reservations("campaign") if item.candidate_digest == second.digest)
    assert reservation.status == "rejected"
    assert reservation.evidence_digest
    ledger = json.loads(
        (tmp_path / "bundles" / "demo" / "negative_result_ledgers" / f"context-bundle-{second.digest}.json").read_text(
            encoding="utf-8"
        )
    )
    assert "is stale" in ledger["entries"][0]["reason"]


def test_stale_candidate_supersedes_prior_risk_authorization(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path / "bundles")
    incumbent = store.bootstrap(_bundle("incumbent"))
    first = _bundle("first", parent=incumbent.digest)
    second = _bundle("second", parent=incumbent.digest)
    store.propose(first, source_run_id="run", source_generation=1)
    store.propose(second, source_run_id="run", source_generation=1)
    controller = CampaignFalsePromotionController(tmp_path / "risk")

    def units(prefix: str, offset: int) -> tuple[ContextBundleEvaluationUnit, ...]:
        return tuple(
            ContextBundleEvaluationUnit(
                f"{prefix}-{unit.fixture}",
                f"{prefix}-{unit.fixture_digest}",
                unit.seed + offset,
                unit.lane,
            )
            for unit in _units()
        )

    first_coordinator = ContextBundlePromotionCoordinator(
        store,
        _WinningEvaluator(first.digest),
        units("first", 0),
        cohort="first",
        policy=_policy(),
        false_promotion_controller=controller,
        campaign_id="campaign",
    )
    second_evaluator = _WinningEvaluator(second.digest)
    second_coordinator = ContextBundlePromotionCoordinator(
        store,
        second_evaluator,
        units("second", 100),
        cohort="second",
        policy=_policy(),
        false_promotion_controller=controller,
        campaign_id="campaign",
    )
    authorize = controller.authorize_promotion

    def authorize_then_promote_sibling(*args: Any, **kwargs: Any) -> Any:
        result = authorize(*args, **kwargs)
        candidate = args[1]
        if candidate.digest == second.digest:
            assert first_coordinator.evaluate_candidate("demo", first.digest).promotion is not None
        return result

    with patch.object(controller, "authorize_promotion", side_effect=authorize_then_promote_sibling):
        result = second_coordinator.evaluate_candidate("demo", second.digest)

    calls = len(second_evaluator.calls)
    replayed = second_coordinator.evaluate_candidate("demo", second.digest)
    reservation = next(item for item in controller.reservations("campaign") if item.candidate_digest == second.digest)
    assert result.promotion is replayed.promotion is None
    # Statistical authorization is immutable history. The later stale-parent
    # disposition supersedes its serving authority without rewriting it.
    assert reservation.status == "authorized"
    assert result.false_promotion_result is not None
    assert replayed.false_promotion_result is not None
    assert result.false_promotion_result.authorized is False
    assert replayed.false_promotion_result.authorized is False
    assert store.candidate("demo", second.digest).lifecycle == BundleLifecycle.REJECTED
    assert store.active_bundle("demo") == first
    assert store.pending_candidates("demo", "run", 1) == ()
    assert len(second_evaluator.calls) == calls


def test_stale_confirmed_restart_terminalizes_from_persisted_plan_after_evaluator_drift(
    tmp_path: Path,
) -> None:
    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    first = _bundle("first", parent=incumbent.digest)
    second = _bundle("second", parent=incumbent.digest)
    store.propose(first, source_run_id="run", source_generation=1)
    store.propose(second, source_run_id="run", source_generation=1)

    class _VersionedWinningEvaluator(_WinningEvaluator):
        def __init__(self, candidate_digest: str, version: int) -> None:
            super().__init__(candidate_digest)
            self.version = version

        def evaluation_plan_identity(self, bundle: ContextBundle) -> Mapping[str, Any]:
            return {**super().evaluation_plan_identity(bundle), "version": self.version}

    class _HoldAudit:
        def review_pre_promotion(
            self,
            candidate: ContextBundle,
            comparison: Any,
            trials: tuple[MatchedTrial, ...],
            *,
            cancellation_event: Any = None,
        ) -> str:
            del candidate, comparison, trials, cancellation_event
            return "review_required"

    held = ContextBundlePromotionCoordinator(
        store,
        _VersionedWinningEvaluator(second.digest, 1),
        _units(),
        cohort="cohort",
        policy=_policy(),
        audit_checkpoint=_HoldAudit(),
    ).evaluate_candidate("demo", second.digest)
    assert held.audit_policy_outcome == "review_required"
    assert (
        ContextBundlePromotionCoordinator(
            store,
            _WinningEvaluator(first.digest),
            _units(),
            cohort="cohort",
            policy=_policy(),
        )
        .evaluate_candidate("demo", first.digest)
        .promotion
        is not None
    )

    changed = _VersionedWinningEvaluator(second.digest, 2)
    result = ContextBundlePromotionCoordinator(
        store,
        changed,
        _units(),
        cohort="cohort",
        policy=_policy(),
    ).evaluate_candidate("demo", second.digest)

    assert result.promotion is None
    assert changed.calls == []
    assert store.candidate("demo", second.digest).lifecycle == BundleLifecycle.REJECTED
    assert store.pending_candidates("demo", "run", 1) == ()


def test_max_budget_inconclusive_is_terminal_and_idempotent(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    incumbent = store.bootstrap(_bundle("incumbent"))
    candidate = _bundle("candidate", parent=incumbent.digest)
    store.propose(candidate, source_run_id="run", source_generation=1)

    class _InconclusiveEvaluator:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def evaluation_plan_identity(self, bundle: ContextBundle) -> Mapping[str, Any]:
            return {"implementation": "inconclusive-v1", "bundle_digest": bundle.digest}

        def evaluate(
            self,
            bundle: ContextBundle,
            unit: ContextBundleEvaluationUnit,
        ) -> ContextBundleEvaluationOutcome:
            self.calls.append((bundle.digest, unit.fixture_digest))
            if bundle.digest != candidate.digest:
                return ContextBundleEvaluationOutcome(score=0.0)
            scores = {"screen": 1.0, "confirm-a": 1.0, "confirm-b": -0.9}
            return ContextBundleEvaluationOutcome(score=scores[unit.fixture_digest])

    evaluator = _InconclusiveEvaluator()
    coordinator = ContextBundlePromotionCoordinator(
        store,
        evaluator,
        _units(),
        cohort="cohort",
        policy=_policy(),
    )
    result = coordinator.evaluate_candidate("demo", candidate.digest)
    evaluated_calls = len(evaluator.calls)
    replayed = coordinator.evaluate_candidate("demo", candidate.digest)

    assert result.comparison.decision == ComparisonDecision.INCONCLUSIVE
    assert replayed.comparison == result.comparison
    assert len(evaluator.calls) == evaluated_calls
    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.REJECTED
    assert store.pending_candidates("demo", "run", 1) == ()
    assert (tmp_path / "demo" / "negative_result_ledgers" / f"context-bundle-{candidate.digest}.json").exists()


def test_coordinator_migrates_reproducible_terminal_schema_v1_without_evaluation(
    tmp_path: Path,
) -> None:
    store, _incumbent, candidate, policy = _confirmed_store(tmp_path)
    evidence_path = store._trials_path("demo", candidate.digest)
    record_path = store._record_path("demo", candidate.digest)
    envelope = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence_path.write_text(json.dumps(envelope["trials"]), encoding="utf-8")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["schema_version"] = 1
    record.pop("confirmation_policy")
    record.pop("confirmation_policy_digest")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    evaluator = _WinningEvaluator(candidate.digest)

    result = ContextBundlePromotionCoordinator(
        store,
        evaluator,
        _units(),
        cohort="cohort",
        policy=policy,
    ).evaluate_candidate("demo", candidate.digest)

    assert result.promotion is not None
    assert result.promotion.confirmation_policy_digest == stable_digest(policy.to_dict())
    assert evaluator.calls == []
    migrated = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["confirmation_policy"] == policy.to_dict()


def test_coordinator_refuses_nonreproducing_terminal_schema_v1_without_rewrite(
    tmp_path: Path,
) -> None:
    store, _incumbent, candidate, policy = _confirmed_store(tmp_path)
    evidence_path = store._trials_path("demo", candidate.digest)
    record_path = store._record_path("demo", candidate.digest)
    envelope = json.loads(evidence_path.read_text(encoding="utf-8"))
    legacy_trials = envelope["trials"]
    evidence_path.write_text(json.dumps(legacy_trials), encoding="utf-8")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["schema_version"] = 1
    record["comparison"]["reason"] = "tampered terminal comparison"
    record.pop("confirmation_policy")
    record.pop("confirmation_policy_digest")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    evaluator = _WinningEvaluator(candidate.digest)

    with pytest.raises(ValueError, match="do not reproduce the terminal comparison"):
        ContextBundlePromotionCoordinator(
            store,
            evaluator,
            _units(),
            cohort="cohort",
            policy=policy,
        ).evaluate_candidate("demo", candidate.digest)

    assert json.loads(evidence_path.read_text(encoding="utf-8")) == legacy_trials
    assert evaluator.calls == []
