from __future__ import annotations

from autocontext.analytics.facets import RunFacet
from autocontext.analytics.rubric_drift import RubricDriftMonitor


def _facet(run_id: str, best: float, epoch: str) -> RunFacet:
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
