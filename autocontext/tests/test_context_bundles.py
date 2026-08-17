"""AC-973: immutable, outcome-gated context bundle tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from autocontext.context_bundles import (
    BundleComponent,
    BundleLifecycle,
    ComparisonDecision,
    ComponentKind,
    ConfirmationPolicy,
    ContextBundle,
    ContextBundleStore,
    MatchedTrial,
    TrialLane,
    evaluate_matched_trials,
)
from autocontext.context_bundles.assembly import build_candidate_bundle, bundle_mutations
from autocontext.harness.mutations import HarnessMutation, MutationType


def _bundle(
    *,
    playbook: str,
    parent: str | None = None,
    epoch: str = "epoch-1",
) -> ContextBundle:
    return ContextBundle.create(
        scenario="demo",
        evaluator_epoch=epoch,
        parent_digest=parent,
        components=[
            BundleComponent(ComponentKind.PLAYBOOK, "playbook", playbook, "text/markdown"),
            BundleComponent.json(ComponentKind.ROUTING_CONFIG, "roles", {"competitor": "small"}),
        ],
    )


def _trials(candidate: ContextBundle, lane: TrialLane, count: int, *, delta: float = 0.2) -> list[MatchedTrial]:
    return [
        MatchedTrial(
            candidate_digest=candidate.digest,
            incumbent_digest=candidate.parent_digest,
            evaluator_epoch=candidate.evaluator_epoch,
            cohort="cohort-a",
            fixture=f"fixture-{lane.value}-{index}",
            fixture_digest=f"fixture-digest-{lane.value}-{index}",
            seed=index,
            lane=lane,
            candidate_score=0.5 + delta,
            incumbent_score=0.5,
        )
        for index in range(count)
    ]


def test_digest_is_stable_across_component_input_order() -> None:
    first = BundleComponent(ComponentKind.HINTS, "hints", "inspect the edge")
    second = BundleComponent(ComponentKind.PLAYBOOK, "playbook", "baseline")
    a = ContextBundle.create(scenario="demo", evaluator_epoch="epoch", components=[first, second])
    b = ContextBundle.create(scenario="demo", evaluator_epoch="epoch", components=[second, first])

    assert a == b
    assert a.digest == b.digest
    assert a.to_dict() == b.to_dict()


def test_manifest_digest_matches_shared_typescript_fixture() -> None:
    fixture_path = Path(__file__).resolve().parents[2] / "fixtures" / "context-bundles" / "manifest-parity.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert ContextBundle.from_dict(fixture["baseline"]) == _bundle(playbook="baseline")
    assert ContextBundle.from_dict(fixture["candidate"]) == _bundle(
        playbook="candidate",
        parent=fixture["baseline"]["digest"],
    )


def test_manifest_rejects_component_or_bundle_digest_tampering() -> None:
    bundle = _bundle(playbook="safe")
    manifest = bundle.to_dict()
    manifest["components"][0]["content"] = "tampered"

    with pytest.raises(ValueError, match="component digest mismatch"):
        ContextBundle.from_dict(manifest)


def test_candidate_mutations_are_resolved_only_from_the_candidate_manifest() -> None:
    baseline = _bundle(playbook="baseline")
    mutation = HarnessMutation(
        mutation_type=MutationType.COMPLETION_CHECK,
        content="verify the final payload",
    )
    candidate = build_candidate_bundle(baseline, evaluator_epoch="epoch-1", mutations=[mutation])

    assert candidate is not None
    assert bundle_mutations(baseline) == []
    assert [item.content for item in bundle_mutations(candidate)] == ["verify the final payload"]


def test_bundle_store_materializes_validators_before_activation(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    bundle = ContextBundle.create(
        scenario="demo",
        evaluator_epoch="epoch-1",
        components=[
            BundleComponent.json(
                ComponentKind.HARNESS_VALIDATOR,
                "validate_output",
                {"name": "validate_output", "code": "def validate(value):\n    return bool(value)"},
            )
        ],
    )

    store.bootstrap(bundle)

    harness_dir = store.runtime_harness_dir("demo", bundle.digest)
    assert harness_dir is not None
    assert (harness_dir / "validate_output.py").read_text(encoding="utf-8") == ("def validate(value):\n    return bool(value)\n")


def test_unmatched_or_duplicate_trials_fail_closed() -> None:
    baseline = _bundle(playbook="baseline")
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    trial = _trials(candidate, TrialLane.SCREEN, 1)[0]
    wrong_epoch = replace(trial, evaluator_epoch="other")

    with pytest.raises(ValueError, match="evaluator epoch"):
        evaluate_matched_trials(candidate, [wrong_epoch])
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_matched_trials(candidate, [trial, trial])


def test_rejected_candidate_keeps_active_pointer_byte_for_byte(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    baseline = store.bootstrap(_bundle(playbook="baseline"))
    candidate = _bundle(playbook="regression", parent=baseline.digest)
    store.propose(candidate, source_run_id="run-1", source_generation=1)
    active_path = tmp_path / "demo" / "context_bundles" / "active.json"
    before = active_path.read_bytes()

    result = store.record_matched_trials(
        "demo",
        candidate.digest,
        _trials(candidate, TrialLane.SCREEN, 2, delta=-0.1),
    )

    assert result.decision == ComparisonDecision.REJECTED
    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.REJECTED
    assert active_path.read_bytes() == before
    assert store.active_bundle("demo") == baseline


def test_inconclusive_candidate_keeps_active_pointer_byte_for_byte(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    baseline = store.bootstrap(_bundle(playbook="baseline"))
    candidate = _bundle(playbook="uncertain", parent=baseline.digest)
    store.propose(candidate, source_run_id="run-1", source_generation=1)
    active_path = tmp_path / "demo" / "context_bundles" / "active.json"
    before = active_path.read_bytes()
    policy = ConfirmationPolicy(min_confirmation_pairs=2, max_confirmation_pairs=2)
    noisy_confirmation = _trials(candidate, TrialLane.CONFIRMATION, 2)
    noisy_confirmation = [
        replace(noisy_confirmation[0], candidate_score=1.0),
        replace(noisy_confirmation[1], candidate_score=0.1),
    ]

    store.record_matched_trials(
        "demo",
        candidate.digest,
        _trials(candidate, TrialLane.SCREEN, 2) + noisy_confirmation,
        policy=policy,
    )

    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.SCREENED
    assert active_path.read_bytes() == before


def test_confirm_promote_and_rollback_switch_complete_bundle_atomically(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    baseline = store.bootstrap(_bundle(playbook="baseline"))
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    store.propose(candidate, source_run_id="run-1", source_generation=2, rationale="improve edges")
    trials = (
        _trials(candidate, TrialLane.SCREEN, 2)
        + _trials(candidate, TrialLane.CONFIRMATION, 6)
        + _trials(candidate, TrialLane.HELDOUT, 2)
    )

    comparison = store.record_matched_trials("demo", candidate.digest, trials)
    promotion = store.promote("demo", candidate.digest, cohort="cohort-a", rationale="confirmed improvement")

    assert comparison.decision == ComparisonDecision.CONFIRMED
    assert store.active_bundle("demo") == candidate
    assert promotion.candidate_digest == candidate.digest
    assert promotion.rollback_target_digest == baseline.digest
    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.ACTIVE
    assert store.candidate("demo", baseline.digest).lifecycle == BundleLifecycle.SUPERSEDED
    promotion_path = tmp_path / "demo" / "context_bundles" / "promotions" / f"{promotion.promotion_id}.json"
    assert json.loads(promotion_path.read_text())["cohort"] == "cohort-a"

    restored = store.rollback("demo", rationale="held-out production regression")
    assert restored == baseline
    assert store.active_bundle("demo") == baseline


def test_promotion_refuses_stale_parent_after_another_candidate_wins(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    baseline = store.bootstrap(_bundle(playbook="baseline"))
    first = _bundle(playbook="first", parent=baseline.digest)
    second = _bundle(playbook="second", parent=baseline.digest)
    for candidate in (first, second):
        store.propose(candidate, source_run_id="run", source_generation=1)
        store.record_matched_trials(
            "demo",
            candidate.digest,
            _trials(candidate, TrialLane.SCREEN, 2)
            + _trials(candidate, TrialLane.CONFIRMATION, 6)
            + _trials(candidate, TrialLane.HELDOUT, 2),
        )
    store.promote("demo", first.digest, cohort="cohort-a", rationale="first winner")

    with pytest.raises(ValueError, match="active bundle changed"):
        store.promote("demo", second.digest, cohort="cohort-a", rationale="stale winner")
