"""AC-973: immutable, outcome-gated context bundle tests."""

from __future__ import annotations

import json
import math
import sys
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
    canonical_json,
    evaluate_matched_trials,
    stable_digest,
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
            BundleComponent.json(ComponentKind.ROUTING_CONFIG, "roles", {"model_competitor": "small"}),
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


def test_canonical_json_matches_shared_unicode_and_numeric_fixture() -> None:
    fixture_path = Path(__file__).resolve().parents[2] / "fixtures" / "context-bundles" / "canonical-parity.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    for case in fixture["canonical_cases"]:
        assert canonical_json(case["value"]) == case["canonical"], case["name"]
        assert stable_digest(case["value"]) == case["digest"], case["name"]
    for case in fixture["unsafe_integer_cases"]:
        value = json.loads(case["json"])
        with pytest.raises(ValueError, match="safe integer"):
            canonical_json(value)
        with pytest.raises(ValueError, match="safe integer"):
            stable_digest(value)
    for case in fixture["invalid_unicode_cases"]:
        value = json.loads(case["json"])
        with pytest.raises(ValueError, match="lone UTF-16 surrogate"):
            canonical_json(value)
        with pytest.raises(ValueError, match="lone UTF-16 surrogate"):
            stable_digest(value)


def test_bundle_digest_matches_shared_unicode_and_numeric_fixture() -> None:
    fixture_path = Path(__file__).resolve().parents[2] / "fixtures" / "context-bundles" / "canonical-parity.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    bundle_input = fixture["bundle_input"]
    bundle = ContextBundle.create(
        scenario=bundle_input["scenario"],
        evaluator_epoch=bundle_input["evaluator_epoch"],
        components=[
            BundleComponent.json(ComponentKind(component["kind"]), component["key"], component["value"])
            for component in bundle_input["components"]
        ],
    )

    assert [component.key for component in bundle.components] == ["😀", "דּ"]
    assert bundle.to_dict() == fixture["bundle_manifest"]
    assert ContextBundle.from_dict(fixture["bundle_manifest"]) == bundle


def test_manifest_rejects_component_or_bundle_digest_tampering() -> None:
    bundle = _bundle(playbook="safe")
    manifest = bundle.to_dict()
    manifest["components"][0]["content"] = "tampered"

    with pytest.raises(ValueError, match="component digest mismatch"):
        ContextBundle.from_dict(manifest)

    forged_digest = bundle.to_dict()
    forged_digest["components"][0]["digest"] = "0" * 64
    with pytest.raises(ValueError, match="component digest mismatch"):
        ContextBundle.from_dict(forged_digest)

    unknown_kind = bundle.to_dict()
    unknown_kind["components"][0]["kind"] = "unknown_kind"
    with pytest.raises(ValueError, match="not a valid ComponentKind"):
        ContextBundle.from_dict(unknown_kind)


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


def test_pair_identity_excludes_lane_and_display_name_in_shared_fixture() -> None:
    fixture_path = Path(__file__).resolve().parents[2] / "fixtures" / "context-bundles" / "canonical-parity.json"
    identity = json.loads(fixture_path.read_text(encoding="utf-8"))["matched_pair_identity"]
    baseline = _bundle(playbook="baseline")
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    screen = MatchedTrial(
        candidate_digest=candidate.digest,
        incumbent_digest=candidate.parent_digest,
        evaluator_epoch=identity["evaluator_epoch"],
        cohort=identity["cohort"],
        fixture=identity["fixture"],
        fixture_digest=identity["fixture_digest"],
        seed=identity["seed"],
        lane=TrialLane.SCREEN,
        candidate_score=0.7,
        incumbent_score=0.5,
    )
    relabeled = replace(screen, lane=TrialLane.CONFIRMATION, fixture="display-confirmation")

    assert screen.pair_key == identity["pair_key"]
    assert relabeled.pair_key == identity["pair_key"]
    assert relabeled.to_dict()["lane"] == "confirmation"
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_matched_trials(candidate, [screen, relabeled])


def test_policy_and_trial_numeric_contracts_fail_closed() -> None:
    invalid_policies = [
        {"min_screen_pairs": 0},
        {"min_confirmation_pairs": 1},
        {"min_confirmation_pairs": 4, "max_confirmation_pairs": 3},
        {"min_heldout_pairs": 0},
        {"min_effect": float("nan")},
        {"confidence_z": float("inf")},
        {"min_screen_pairs": 1.5},
    ]
    for values in invalid_policies:
        with pytest.raises(ValueError):
            ConfirmationPolicy(**values)  # type: ignore[arg-type]

    baseline = _bundle(playbook="baseline")
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    trial = _trials(candidate, TrialLane.SCREEN, 1)[0]
    invalid_trials = [
        replace(trial, candidate_score=float("nan")),
        replace(trial, incumbent_score=float("inf")),
        replace(trial, candidate_score=1e308, incumbent_score=-1e308),
    ]
    for invalid in invalid_trials:
        with pytest.raises(ValueError, match="finite"):
            evaluate_matched_trials(candidate, [invalid])


@pytest.mark.parametrize(
    ("field_name", "malformed_value"),
    [
        ("seed", True),
        ("seed", 1.5),
        ("candidate_valid", "false"),
        ("incumbent_valid", 1),
        ("candidate_score", "0.7"),
        ("incumbent_score", None),
        ("candidate_digest", 123),
        ("pair_key", 123),
    ],
)
def test_matched_trial_replay_rejects_coercible_raw_types(field_name: str, malformed_value: object) -> None:
    baseline = _bundle(playbook="baseline")
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    payload = _trials(candidate, TrialLane.SCREEN, 1)[0].to_dict()
    payload[field_name] = malformed_value

    with pytest.raises((TypeError, ValueError)):
        MatchedTrial.from_dict(payload)


def test_matched_trial_replay_accepts_the_legacy_pair_key() -> None:
    baseline = _bundle(playbook="baseline")
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    trial = _trials(candidate, TrialLane.SCREEN, 1)[0]
    payload = trial.to_dict()
    payload["pair_key"] = trial.legacy_pair_key

    assert MatchedTrial.from_dict(payload) == trial


def test_legacy_matched_artifact_rejects_rows_that_collapse_to_one_current_pair(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    baseline = store.bootstrap(_bundle(playbook="baseline"))
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    store.propose(candidate, source_run_id="run-1", source_generation=1)
    screen = _trials(candidate, TrialLane.SCREEN, 1)[0]
    relabeled = replace(screen, fixture="renamed", lane=TrialLane.CONFIRMATION)
    legacy_rows = []
    for trial in (screen, relabeled):
        payload = trial.to_dict()
        payload["pair_key"] = trial.legacy_pair_key
        legacy_rows.append(payload)
    trials_path = tmp_path / "demo" / "context_bundles" / "candidates" / candidate.digest / "matched_trials.json"
    trials_path.write_text(json.dumps(legacy_rows), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate current pair identity"):
        store.matched_trials("demo", candidate.digest)


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


def test_confirmation_uses_student_t_and_bonferroni_bounds_matching_typescript() -> None:
    baseline = _bundle(playbook="baseline")
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    screen = _trials(candidate, TrialLane.SCREEN, 2, delta=0.02)
    confirmation = _trials(candidate, TrialLane.CONFIRMATION, 4)
    confirmation = [
        replace(trial, candidate_score=0.5 + delta)
        for trial, delta in zip(confirmation, [0.014, 0.014, 0.034, 0.034], strict=True)
    ]
    trials = screen + confirmation

    fixed_look = evaluate_matched_trials(
        candidate,
        trials,
        policy=ConfirmationPolicy(
            min_confirmation_pairs=4,
            max_confirmation_pairs=4,
            min_effect=0.01,
        ),
    )

    assert fixed_look.decision == ComparisonDecision.INCONCLUSIVE
    assert fixed_look.mean_effect == pytest.approx(0.02400000000000002)
    assert fixed_look.confidence_low == pytest.approx(0.005625504524827234)
    assert fixed_look.confidence_high == pytest.approx(0.04237449547517281)

    adaptive_looks = evaluate_matched_trials(
        candidate,
        trials,
        policy=ConfirmationPolicy(
            min_confirmation_pairs=4,
            max_confirmation_pairs=12,
            min_effect=0.01,
        ),
    )

    assert adaptive_looks.decision == ComparisonDecision.NEEDS_CONFIRMATION
    assert adaptive_looks.confidence_low == pytest.approx(-0.017483077826851476)
    assert adaptive_looks.confidence_high == pytest.approx(0.06548307782685152)

    decisive_over_budget = (
        screen + _trials(candidate, TrialLane.CONFIRMATION, 5, delta=0.2) + _trials(candidate, TrialLane.HELDOUT, 2, delta=0.2)
    )
    with pytest.raises(ValueError, match="confirmation pairs exceed"):
        evaluate_matched_trials(
            candidate,
            decisive_over_budget,
            policy=ConfirmationPolicy(min_confirmation_pairs=4, max_confirmation_pairs=4),
        )


def test_high_confidence_student_t_bounds_are_finite_and_conservative() -> None:
    from autocontext.analytics.paired_statistics import paired_confidence_interval

    mean, low, high = paired_confidence_interval([0.1, 0.2, 0.15], 8.0, max_looks=15)

    assert mean == pytest.approx(0.15)
    assert low is not None and math.isfinite(low)
    assert high is not None and math.isfinite(high)
    assert low < mean < high

    _, underflow_low, underflow_high = paired_confidence_interval([0.1, 0.2, 0.15], 40.0, max_looks=15)
    assert underflow_low == -sys.float_info.max
    assert underflow_high == sys.float_info.max


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
    assert promotion.confirmation_policy == ConfirmationPolicy()
    assert promotion.confirmation_policy_digest == stable_digest(ConfirmationPolicy().to_dict())
    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.ACTIVE
    assert store.candidate("demo", baseline.digest).lifecycle == BundleLifecycle.SUPERSEDED
    promotion_path = tmp_path / "demo" / "context_bundles" / "promotions" / f"{promotion.promotion_id}.json"
    assert json.loads(promotion_path.read_text())["cohort"] == "cohort-a"
    assert json.loads(promotion_path.read_text())["confirmation_policy"] == ConfirmationPolicy().to_dict()

    restored = store.rollback("demo", rationale="held-out production regression")
    assert restored == baseline
    assert store.active_bundle("demo") == baseline


def test_promotion_replays_raw_trials_under_the_persisted_policy(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    baseline = store.bootstrap(_bundle(playbook="baseline"))
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    store.propose(candidate, source_run_id="run-1", source_generation=2)
    store.record_matched_trials(
        "demo",
        candidate.digest,
        _trials(candidate, TrialLane.SCREEN, 2)
        + _trials(candidate, TrialLane.CONFIRMATION, 6)
        + _trials(candidate, TrialLane.HELDOUT, 2),
    )
    record_path = tmp_path / "demo" / "context_bundles" / "candidates" / candidate.digest / "record.json"
    tampered = json.loads(record_path.read_text(encoding="utf-8"))
    tampered["comparison"]["mean_effect"] = 99.0
    record_path.write_text(json.dumps(tampered), encoding="utf-8")
    active_path = tmp_path / "demo" / "context_bundles" / "active.json"
    before = active_path.read_bytes()

    with pytest.raises(ValueError, match="do not reproduce"):
        store.promote("demo", candidate.digest, cohort="cohort-a", rationale="tampered")

    assert active_path.read_bytes() == before
    assert store.active_bundle("demo") == baseline


def test_matched_evidence_cannot_change_confirmation_policy_midstream(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    baseline = store.bootstrap(_bundle(playbook="baseline"))
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    store.propose(candidate, source_run_id="run-1", source_generation=2)
    policy = ConfirmationPolicy(min_confirmation_pairs=4, max_confirmation_pairs=8)
    store.record_matched_trials(
        "demo",
        candidate.digest,
        _trials(candidate, TrialLane.SCREEN, 2),
        policy=policy,
    )

    with pytest.raises(ValueError, match="policy cannot change"):
        store.record_matched_trials(
            "demo",
            candidate.digest,
            _trials(candidate, TrialLane.CONFIRMATION, 6),
            policy=ConfirmationPolicy(),
        )


def test_matched_evidence_policy_commit_recovers_after_candidate_record_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autocontext.context_bundles.store as store_module

    store = ContextBundleStore(tmp_path)
    baseline = store.bootstrap(_bundle(playbook="baseline"))
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    store.propose(candidate, source_run_id="run-1", source_generation=2)
    policy = ConfirmationPolicy(min_confirmation_pairs=4, max_confirmation_pairs=8)
    trials = _trials(candidate, TrialLane.SCREEN, 2)
    record_path = tmp_path / "demo" / "context_bundles" / "candidates" / candidate.digest / "record.json"
    evidence_path = record_path.with_name("matched_trials.json")
    original_write_json = store_module.write_json
    failed = False

    def fail_candidate_update_once(path: Path, data: dict[str, object] | list[object], **kwargs: object) -> None:
        nonlocal failed
        if path == record_path and isinstance(data, dict) and data.get("confirmation_policy") is not None and not failed:
            failed = True
            raise OSError("simulated candidate-record write failure")
        original_write_json(path, data, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store_module, "write_json", fail_candidate_update_once)
    with pytest.raises(OSError, match="simulated candidate-record write failure"):
        store.record_matched_trials("demo", candidate.digest, trials, policy=policy)

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == 2
    assert evidence["confirmation_policy"] == policy.to_dict()
    assert evidence["confirmation_policy_digest"] == stable_digest(policy.to_dict())
    assert store.candidate("demo", candidate.digest).confirmation_policy is None

    monkeypatch.setattr(store_module, "write_json", original_write_json)
    comparison = store.record_matched_trials("demo", candidate.digest, trials, policy=policy)

    assert comparison.decision == ComparisonDecision.NEEDS_CONFIRMATION
    recovered = store.candidate("demo", candidate.digest)
    assert recovered.schema_version == 2
    assert recovered.confirmation_policy == policy.to_dict()
    assert recovered.confirmation_policy_digest == stable_digest(policy.to_dict())


def test_terminal_legacy_evidence_migration_cannot_add_or_change_trials(tmp_path: Path) -> None:
    store = ContextBundleStore(tmp_path)
    baseline = store.bootstrap(_bundle(playbook="baseline"))
    candidate = _bundle(playbook="candidate", parent=baseline.digest)
    store.propose(candidate, source_run_id="run-1", source_generation=2)
    trials = (
        _trials(candidate, TrialLane.SCREEN, 2)
        + _trials(candidate, TrialLane.CONFIRMATION, 6)
        + _trials(candidate, TrialLane.HELDOUT, 2)
    )
    comparison = store.record_matched_trials("demo", candidate.digest, trials)
    assert comparison.decision == ComparisonDecision.CONFIRMED

    candidate_dir = tmp_path / "demo" / "context_bundles" / "candidates" / candidate.digest
    record_path = candidate_dir / "record.json"
    evidence_path = candidate_dir / "matched_trials.json"
    legacy_rows = []
    for trial in trials:
        payload = trial.to_dict()
        payload["pair_key"] = trial.legacy_pair_key
        legacy_rows.append(payload)
    evidence_path.write_text(json.dumps(legacy_rows), encoding="utf-8")
    legacy_record = json.loads(record_path.read_text(encoding="utf-8"))
    legacy_record["schema_version"] = 1
    legacy_record.pop("confirmation_policy")
    legacy_record.pop("confirmation_policy_digest")
    record_path.write_text(json.dumps(legacy_record), encoding="utf-8")

    new_trial = replace(
        trials[0],
        fixture="new-fixture",
        fixture_digest="new-fixture-digest",
        seed=999,
    )
    with pytest.raises(ValueError, match="cannot add trials to a confirmed bundle"):
        store.record_matched_trials("demo", candidate.digest, [new_trial])
    assert isinstance(json.loads(evidence_path.read_text(encoding="utf-8")), list)

    legacy_record["lifecycle"] = "rejected"
    record_path.write_text(json.dumps(legacy_record), encoding="utf-8")
    with pytest.raises(ValueError, match="does not reproduce the terminal lifecycle"):
        store.record_matched_trials("demo", candidate.digest, [], policy=ConfirmationPolicy())
    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.REJECTED
    assert isinstance(json.loads(evidence_path.read_text(encoding="utf-8")), list)

    legacy_record["lifecycle"] = "confirmed"
    record_path.write_text(json.dumps(legacy_record), encoding="utf-8")

    replay = store.record_matched_trials("demo", candidate.digest, [], policy=ConfirmationPolicy())
    assert replay.to_dict() == comparison.to_dict()
    migrated_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert migrated_evidence["schema_version"] == 2
    assert store.candidate("demo", candidate.digest).schema_version == 2


@pytest.mark.parametrize(
    ("routing", "message"),
    [
        (
            {
                "model_competitor": "small",
                "dag_changes": [{"action": "remove_role", "name": "coach"}],
            },
            "orchestrator reconstruction",
        ),
        (
            {"model_competitor": "small", "tuning_proposal": "{}"},
            "gate and executor reconstruction",
        ),
        (
            {"model_competitor": "small", "agent_provider": "other"},
            "construction-bound routing field",
        ),
        (
            {"model_competitor": "small", "unknown_route": True},
            "unsupported routing fields",
        ),
    ],
)
def test_promotion_rejects_bundle_that_the_serving_lifecycle_cannot_apply(
    tmp_path: Path,
    routing: dict[str, object],
    message: str,
) -> None:
    store = ContextBundleStore(tmp_path)
    baseline = store.bootstrap(_bundle(playbook="baseline"))
    candidate = ContextBundle.create(
        scenario="demo",
        evaluator_epoch="epoch-1",
        parent_digest=baseline.digest,
        components=[
            BundleComponent(ComponentKind.PLAYBOOK, "playbook", "candidate", "text/markdown"),
            BundleComponent.json(ComponentKind.ROUTING_CONFIG, "roles", routing),
        ],
    )
    store.propose(candidate, source_run_id="run-1", source_generation=2)
    store.record_matched_trials(
        "demo",
        candidate.digest,
        _trials(candidate, TrialLane.SCREEN, 2)
        + _trials(candidate, TrialLane.CONFIRMATION, 6)
        + _trials(candidate, TrialLane.HELDOUT, 2),
    )
    active_path = tmp_path / "demo" / "context_bundles" / "active.json"
    before = active_path.read_bytes()

    with pytest.raises(ValueError, match=message):
        store.promote("demo", candidate.digest, cohort="cohort-a", rationale="must not activate")

    assert active_path.read_bytes() == before
    assert store.active_bundle("demo") == baseline
    assert store.candidate("demo", candidate.digest).lifecycle == BundleLifecycle.CONFIRMED


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
