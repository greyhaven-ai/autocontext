from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "docs" / "context-attribution-parity-fixture.json"
ROUNDING_FIXTURE = ROOT / "fixtures" / "numeric-rounding-parity.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_single_component_causal_credit_is_reconstructed_from_trials() -> None:
    from autocontext.analytics.context_attribution import (
        ControlledAttributionTrial,
        attribute_controlled_trials,
        reconstruct_causal_credit,
    )

    fixture = _fixture()
    trials = [ControlledAttributionTrial.from_dict(item) for item in fixture["initial_trials"]]
    record = attribute_controlled_trials(trials, evaluator_epoch=fixture["evaluator_epoch"])[0]

    for key, expected in fixture["initial_expected"].items():
        actual = getattr(record, key)
        assert (actual.value if hasattr(actual, "value") else actual) == expected
    assert reconstruct_causal_credit(record, trials) == 0.2


def test_attribution_identity_uses_shared_utf16_trial_id_order() -> None:
    from autocontext.analytics.context_attribution import ControlledAttributionTrial, attribute_controlled_trials

    fixture = _fixture()
    case = fixture["unicode_trial_order"]
    trials = [ControlledAttributionTrial.from_dict(item) for item in fixture["initial_trials"]]
    trials = [
        trial.model_copy(update={"trial_id": trial_id})
        for trial, trial_id in zip(trials, case["input_trial_ids"], strict=True)
    ]

    record = attribute_controlled_trials(trials, evaluator_epoch=fixture["evaluator_epoch"])[0]

    assert record.trial_ids == case["expected_trial_ids"]
    assert record.attribution_id == case["expected_attribution_id"]


def test_attribution_effect_rounding_matches_shared_half_away_from_zero_fixture() -> None:
    from autocontext.analytics.context_attribution import ControlledAttributionTrial, attribute_controlled_trials

    base = _fixture()["initial_trials"][0]
    cases = json.loads(ROUNDING_FIXTURE.read_text(encoding="utf-8"))["cases"]
    for index, case in enumerate(cases):
        trial = ControlledAttributionTrial.from_dict(
            {
                **base,
                "trial_id": f"rounding-{index}",
                "fixture_digest": f"sha256:rounding-{index}",
                "seed": index,
                "with_component_score": case["value"],
                "without_component_score": 0.0,
            }
        )
        assert trial.effect == case["expected"], case["name"]
        record = attribute_controlled_trials([trial], evaluator_epoch=trial.evaluator_epoch)[0]
        assert record.effect == case["expected"], case["name"]


def test_reablation_after_bundle_change_preserves_history_and_finds_harm() -> None:
    from autocontext.analytics.context_attribution import (
        ContextAttributionLedger,
        ControlledAttributionTrial,
        append_reablation,
        attribute_controlled_trials,
    )

    fixture = _fixture()
    initial_trials = [ControlledAttributionTrial.from_dict(item) for item in fixture["initial_trials"]]
    initial = attribute_controlled_trials(initial_trials, evaluator_epoch=fixture["evaluator_epoch"])
    ledger = ContextAttributionLedger(
        scenario="grid_ctf",
        trials=initial_trials,
        attributions=initial,
    )
    reablation_trials = [ControlledAttributionTrial.from_dict(item) for item in fixture["reablation_trials"]]
    reablation = attribute_controlled_trials(reablation_trials, evaluator_epoch=fixture["evaluator_epoch"])

    updated = append_reablation(ledger, trials=reablation_trials, attributions=reablation)

    assert len(updated.attributions) == 2
    latest = updated.attributions[-1]
    for key, expected in fixture["reablation_expected"].items():
        actual = getattr(latest, key)
        assert (actual.value if hasattr(actual, "value") else actual) == expected
    assert latest.supersedes_attribution_id == initial[0].attribution_id
    assert updated.attributions[0].disposition == "retained"


def test_reablation_respects_budget_and_rejects_evaluator_mismatch() -> None:
    from autocontext.analytics.context_attribution import (
        ControlledAttributionTrial,
        ReablationCandidate,
        ReablationPolicy,
        attribute_controlled_trials,
        plan_reablation,
    )

    fixture = _fixture()
    case = fixture["budget_case"]
    plan = plan_reablation(
        [ReablationCandidate.from_dict(item) for item in case["candidates"]],
        current_generation=case["current_generation"],
        last_reablation_generation=case["last_reablation_generation"],
        plateau_length=case["plateau_length"],
        current_bundle_digest=case["current_bundle_digest"],
        policy=ReablationPolicy.from_dict(case["policy"]),
    )
    assert [item.component_digest for item in plan.selected] == case["expected_selected"]
    assert [item.component_digest for item in plan.deferred] == case["expected_deferred"]
    assert plan.spent <= plan.budget

    mismatched = ControlledAttributionTrial.from_dict(fixture["initial_trials"][0]).model_copy(
        update={"evaluator_epoch": "eval-8"}
    )
    with pytest.raises(ValueError, match="evaluator epoch mismatch"):
        attribute_controlled_trials([mismatched], evaluator_epoch="eval-7")


def test_controlled_attribution_rejects_self_comparisons_duplicate_pairs_and_mixed_context() -> None:
    from autocontext.analytics.context_attribution import (
        ControlledAttributionTrial,
        attribute_controlled_trials,
    )

    fixture = _fixture()
    trials = [ControlledAttributionTrial.from_dict(item) for item in fixture["initial_trials"]]
    first, second = trials

    self_comparison = first.model_copy(update={"comparison_bundle_digest": first.tested_bundle_digest})
    with pytest.raises(ValueError, match="distinct tested and comparison bundles"):
        attribute_controlled_trials([self_comparison], evaluator_epoch=fixture["evaluator_epoch"])

    duplicate_pair = first.model_copy(update={"trial_id": "duplicate-pair-with-a-new-id"})
    with pytest.raises(ValueError, match="duplicate matched attribution pair"):
        attribute_controlled_trials([first, duplicate_pair], evaluator_epoch=fixture["evaluator_epoch"])

    mixed_comparison = second.model_copy(update={"comparison_bundle_digest": "sha256:other-comparison"})
    with pytest.raises(ValueError, match="mixes comparison bundles"):
        attribute_controlled_trials([first, mixed_comparison], evaluator_epoch=fixture["evaluator_epoch"])

    mixed_cohort = second.model_copy(update={"trial_cohort": "cohort-b"})
    with pytest.raises(ValueError, match="mixes trial cohorts"):
        attribute_controlled_trials([first, mixed_cohort], evaluator_epoch=fixture["evaluator_epoch"])


def test_causal_replay_rejects_duplicate_tampered_and_self_asserted_evidence() -> None:
    from autocontext.analytics.context_attribution import (
        ComponentAttribution,
        ContextAttributionLedger,
        ControlledAttributionTrial,
        append_reablation,
        attribute_controlled_trials,
        reconstruct_causal_credit,
    )

    fixture = _fixture()
    trials = [ControlledAttributionTrial.from_dict(item) for item in fixture["initial_trials"]]
    record = attribute_controlled_trials(trials, evaluator_epoch=fixture["evaluator_epoch"])[0]

    assert record.comparison_bundle_digest == "sha256:bundle-a-minus-playbook"
    assert record.classification_neutral_effect == 0.0
    assert record.classification_high_token_cost == 256
    assert len(record.matched_pair_keys) == len(trials)
    assert len(record.source_trial_digests) == len(trials)

    with pytest.raises(ValueError, match="duplicate attribution trial"):
        reconstruct_causal_credit(record, [*trials, trials[0]])

    tampered_trial = trials[0].model_copy(update={"fixture_digest": "sha256:different-fixture"})
    with pytest.raises(ValueError, match="binding mismatch"):
        reconstruct_causal_credit(record, [tampered_trial, trials[1]])

    self_asserted_effect = record.model_copy(update={"effect": 1.0})
    with pytest.raises(ValueError, match="effect does not match"):
        reconstruct_causal_credit(self_asserted_effect, trials)

    self_asserted_binding = record.model_copy(update={"comparison_bundle_digest": "sha256:invented"})
    with pytest.raises(ValueError, match="does not match the controlled attribution"):
        reconstruct_causal_credit(self_asserted_binding, trials)

    self_asserted_disposition = record.model_copy(update={"disposition": "harmful"})
    with pytest.raises(ValueError, match="disposition does not match"):
        reconstruct_causal_credit(self_asserted_disposition, trials)

    missing_policy = record.to_dict()
    missing_policy.pop("classification_neutral_effect")
    with pytest.raises(ValueError, match="classification_neutral_effect"):
        ComponentAttribution.from_dict(missing_policy)

    ledger = ContextAttributionLedger(scenario="grid_ctf", trials=[], attributions=[])
    with pytest.raises(ValueError, match="source-trial binding mismatch"):
        append_reablation(
            ledger,
            trials=trials,
            attributions=[record.model_copy(update={"source_trial_digests": []})],
        )
    with pytest.raises(ValueError, match="disposition does not match"):
        append_reablation(ledger, trials=trials, attributions=[self_asserted_disposition])


def test_schema_v1_ledger_migrates_history_without_verifying_invented_provenance() -> None:
    from autocontext.analytics.context_attribution import (
        ContextAttributionLedger,
        ControlledAttributionTrial,
        append_reablation,
        attribute_controlled_trials,
        reconstruct_causal_credit,
    )

    fixture = _fixture()
    trials = [ControlledAttributionTrial.from_dict(item) for item in fixture["initial_trials"]]
    current = attribute_controlled_trials(trials, evaluator_epoch=fixture["evaluator_epoch"])[0]
    legacy_record = current.to_dict()
    for field_name in (
        "comparison_bundle_digest",
        "classification_neutral_effect",
        "classification_high_token_cost",
        "matched_pair_keys",
        "source_trial_digests",
        "legacy_unverified",
    ):
        legacy_record.pop(field_name)
    migrated = ContextAttributionLedger.from_dict(
        {
            "schema_version": 1,
            "scenario": "grid_ctf",
            "trials": [trial.to_dict() for trial in trials],
            "attributions": [legacy_record],
        }
    )

    assert migrated.schema_version == 2
    assert migrated.attributions[0].legacy_unverified is True
    with pytest.raises(ValueError, match="legacy attribution lacks verified controlled-trial provenance"):
        reconstruct_causal_credit(migrated.attributions[0], trials)

    new_trials = [ControlledAttributionTrial.from_dict(item) for item in fixture["reablation_trials"]]
    new_records = attribute_controlled_trials(new_trials, evaluator_epoch=fixture["evaluator_epoch"])
    updated = append_reablation(migrated, trials=new_trials, attributions=new_records)
    assert updated.attributions[-1].legacy_unverified is False
    assert updated.attributions[-1].supersedes_attribution_id == migrated.attributions[0].attribution_id


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
def test_controlled_attribution_rejects_nonfinite_scores_and_thresholds(score: float) -> None:
    from autocontext.analytics.context_attribution import (
        ControlledAttributionTrial,
        attribute_controlled_trials,
    )

    fixture = _fixture()
    raw = {**fixture["initial_trials"][0], "with_component_score": score}
    with pytest.raises(ValueError, match="finite"):
        ControlledAttributionTrial.from_dict(raw)

    trial = ControlledAttributionTrial.from_dict(fixture["initial_trials"][0])
    overflowing = trial.model_copy(update={"with_component_score": 1e308, "without_component_score": -1e308})
    with pytest.raises(ValueError, match="effect must be finite"):
        attribute_controlled_trials([overflowing], evaluator_epoch=fixture["evaluator_epoch"])
    with pytest.raises(ValueError, match="neutral_effect must be finite"):
        attribute_controlled_trials(
            [trial],
            evaluator_epoch=fixture["evaluator_epoch"],
            neutral_effect=score,
        )


def test_prompt_selection_demotes_neutral_high_cost_component_but_retests_interactions() -> None:
    from autocontext.analytics.context_attribution import (
        ControlledAttributionTrial,
        attribute_controlled_trials,
        select_prompt_components,
    )
    from autocontext.context_bundles import BundleComponent, ComponentKind, ContextBundle

    playbook = BundleComponent(kind=ComponentKind.PLAYBOOK, key="main", content="costly guidance " * 30)
    original_bundle = ContextBundle.create(scenario="grid_ctf", evaluator_epoch="eval-7", components=[playbook])
    trial = ControlledAttributionTrial(
        trial_id="neutral-playbook",
        component_kind=playbook.kind.value,
        component_key=playbook.key,
        component_digest=playbook.digest,
        tested_bundle_digest=original_bundle.digest,
        comparison_bundle_digest="sha256:without-playbook",
        evaluator_epoch="eval-7",
        trial_cohort="cohort-a",
        fixture_digest="sha256:fixture",
        seed=1,
        evidence_level="causal_ablation",
        with_component_score=0.7,
        without_component_score=0.7,
        token_cost=500,
        tested_at="2026-08-17T12:00:00Z",
    )
    record = attribute_controlled_trials([trial], evaluator_epoch="eval-7")[0]

    selection = select_prompt_components(original_bundle, [record])[0]
    assert selection.disposition == "demotion_candidate"
    assert selection.included is False

    hints = BundleComponent(kind=ComponentKind.HINTS, key="coach", content="new interaction")
    changed_bundle = ContextBundle.create(scenario="grid_ctf", evaluator_epoch="eval-7", components=[playbook, hints])
    changed_selection = next(
        item for item in select_prompt_components(changed_bundle, [record]) if item.component_digest == playbook.digest
    )
    assert changed_selection.included is True
    assert changed_selection.disposition == "uncertain"
    assert "interaction re-ablation" in changed_selection.reason

    positive_trial = trial.model_copy(
        update={
            "trial_id": "positive-playbook",
            "fixture_digest": "sha256:positive-fixture",
            "with_component_score": 0.8,
            "without_component_score": 0.7,
        }
    )
    retained = attribute_controlled_trials([positive_trial], evaluator_epoch="eval-7")[0]
    assert retained.disposition == "retained"
    tampered_selection = select_prompt_components(
        original_bundle,
        [retained.model_copy(update={"disposition": "harmful"})],
    )[0]
    assert tampered_selection.included is True
    assert tampered_selection.disposition == "uncertain"
    assert "failed classification-policy verification" in tampered_selection.reason


def test_edit_size_credit_and_reports_are_explicitly_noncausal() -> None:
    from autocontext.analytics.context_attribution import (
        ComponentAttribution,
        render_context_attribution_report,
    )
    from autocontext.analytics.credit_assignment import (
        ComponentChange,
        GenerationChangeVector,
        attribute_credit,
        format_attribution_for_agent,
    )

    correlated = attribute_credit(
        GenerationChangeVector(
            generation=1,
            score_delta=0.2,
            changes=[ComponentChange(component="playbook", magnitude=1.0, description="changed")],
        )
    )
    assert correlated.metadata == {"evidence_level": "component_correlated", "causal": False}
    assert "not causal" in format_attribution_for_agent(correlated, "coach")

    record = ComponentAttribution(
        attribution_id="correlated-1",
        component_kind="playbook",
        component_key="main",
        component_digest="sha256:playbook",
        evidence_level="component_correlated",
        effect=0.2,
        confidence=0.2,
        evaluator_epoch="eval-7",
        trial_cohort="generation-1",
        token_cost=100,
        last_tested_bundle_digest="sha256:bundle",
        tested_at="2026-08-17T12:00:00Z",
        disposition="uncertain",
        classification_neutral_effect=0.0,
        classification_high_token_cost=256,
        trial_ids=[],
        interaction_component_digests=[],
    )
    report = render_context_attribution_report([record])
    assert "component_correlated" in report
    assert "not causal" in report
