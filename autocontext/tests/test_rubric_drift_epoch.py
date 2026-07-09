from __future__ import annotations

from autocontext.analytics.facets import RunFacet
from autocontext.analytics.rubric_drift import RubricDriftMonitor


def _facet(run_id: str, best: float, epoch: str | None) -> RunFacet:
    return RunFacet(
        run_id=run_id,
        scenario="s",
        scenario_family="f",
        agent_provider="anthropic",
        executor_mode="local",
        total_generations=1,
        advances=1,
        retries=0,
        rollbacks=0,
        best_score=best,
        best_elo=0.0,
        total_duration_seconds=0.0,
        total_tokens=0,
        total_cost_usd=0.0,
        tool_invocations=0,
        validation_failures=0,
        consultation_count=0,
        consultation_cost_usd=0.0,
        friction_signals=[],
        delight_signals=[],
        events=[],
        evaluator_epoch=epoch,
    )


def test_snapshot_flags_mixed_epoch() -> None:
    mon = RubricDriftMonitor()
    single = mon.compute_snapshot([_facet("a", 0.9, "e1"), _facet("b", 0.8, "e1")])
    assert single.mixed_epoch is False and single.evaluator_epochs == ["e1"]
    mixed = mon.compute_snapshot([_facet("a", 0.9, "e1"), _facet("b", 0.8, "e2")])
    assert mixed.mixed_epoch is True and mixed.evaluator_epochs == ["e1", "e2"]
    # math unchanged: mean over the same scores regardless of epoch
    assert mixed.mean_score == single.mean_score  # both means of {0.9, 0.8}


def test_snapshot_known_plus_unknown_epoch_is_mixed() -> None:
    mon = RubricDriftMonitor()
    # None is its own class: a known epoch mixed with a null spans two classes.
    known_unknown = mon.compute_snapshot([_facet("a", 0.9, "e1"), _facet("b", 0.8, None)])
    assert known_unknown.mixed_epoch is True
    assert known_unknown.has_unknown_epoch is True
    assert known_unknown.evaluator_epochs == ["e1"]  # only KNOWN epochs displayed
    # All-null is a single unknown class -> not mixed.
    all_unknown = mon.compute_snapshot([_facet("a", 0.9, None), _facet("b", 0.8, None)])
    assert all_unknown.mixed_epoch is False
    assert all_unknown.has_unknown_epoch is True
    assert all_unknown.evaluator_epochs == []


def test_baseline_inflation_warning_reflects_both_epochs() -> None:
    mon = RubricDriftMonitor()
    # Homogeneous e1 baseline vs homogeneous e2 current: the mean-score delta is a
    # cross-evaluator comparison, so the inflation warning must be flagged mixed.
    baseline = mon.compute_snapshot([_facet("a", 0.5, "e1"), _facet("b", 0.5, "e1")])
    current_e2 = mon.compute_snapshot([_facet("c", 0.9, "e2"), _facet("d", 0.9, "e2")])
    warnings = mon.detect_drift(current_e2, baseline)
    inflation = [w for w in warnings if w.metric_name == "mean_score_delta"]
    assert len(inflation) == 1
    assert current_e2.mixed_epoch is False  # current alone is single-epoch
    assert inflation[0].mixed_epoch is True  # but the comparison spans e1 + e2

    # Same-epoch baseline/current: the inflation warning is not cross-evaluator.
    current_e1 = mon.compute_snapshot([_facet("c", 0.9, "e1"), _facet("d", 0.9, "e1")])
    warnings_same = mon.detect_drift(current_e1, baseline)
    inflation_same = [w for w in warnings_same if w.metric_name == "mean_score_delta"]
    assert len(inflation_same) == 1
    assert inflation_same[0].mixed_epoch is False
