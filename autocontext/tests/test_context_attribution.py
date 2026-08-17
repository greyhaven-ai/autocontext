from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "docs" / "context-attribution-parity-fixture.json"


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
    reablation_trials = [
        ControlledAttributionTrial.from_dict(item) for item in fixture["reablation_trials"]
    ]
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


def test_prompt_selection_demotes_neutral_high_cost_component_but_retests_interactions() -> None:
    from autocontext.analytics.context_attribution import (
        ControlledAttributionTrial,
        attribute_controlled_trials,
        select_prompt_components,
    )
    from autocontext.context_bundles import BundleComponent, ComponentKind, ContextBundle

    playbook = BundleComponent(kind=ComponentKind.PLAYBOOK, key="main", content="costly guidance " * 30)
    original_bundle = ContextBundle.create(
        scenario="grid_ctf", evaluator_epoch="eval-7", components=[playbook]
    )
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
    changed_bundle = ContextBundle.create(
        scenario="grid_ctf", evaluator_epoch="eval-7", components=[playbook, hints]
    )
    changed_selection = next(
        item for item in select_prompt_components(changed_bundle, [record]) if item.component_digest == playbook.digest
    )
    assert changed_selection.included is True
    assert changed_selection.disposition == "uncertain"
    assert "interaction re-ablation" in changed_selection.reason


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
        trial_ids=[],
        interaction_component_digests=[],
    )
    report = render_context_attribution_report([record])
    assert "component_correlated" in report
    assert "not causal" in report
