"""Tests for AC-145: extracted retry, gate, and side-effect helpers from stage_tournament.

Covers: resolve_gate_decision, build_retry_prompt, apply_tournament_outcome,
build_validity_rollback.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Lightweight test doubles for tournament results
# ---------------------------------------------------------------------------


def _make_eval_result(score: float = 0.7, **meta: Any) -> Any:
    from autocontext.harness.evaluation.types import EvaluationResult

    exec_output = type("ExecOutput", (), {"result": type("R", (), {"replay": {}})()})()
    return EvaluationResult(
        score=score,
        passed=True,
        errors=[],
        metadata={"execution_output": exec_output, **meta},
    )


def _make_tournament(
    best_score: float = 0.7,
    mean_score: float = 0.65,
    wins: int = 3,
    losses: int = 2,
    elo_after: float = 1050.0,
    n_results: int = 5,
) -> Any:
    from autocontext.harness.evaluation.types import EvaluationSummary

    results = [_make_eval_result(best_score if i == 0 else mean_score) for i in range(n_results)]
    return EvaluationSummary(
        mean_score=mean_score,
        best_score=best_score,
        wins=wins,
        losses=losses,
        elo_after=elo_after,
        results=results,
    )


# ===========================================================================
# resolve_gate_decision
# ===========================================================================


class TestResolveGateDecision:
    def test_rapid_advance_on_improvement(self) -> None:
        from autocontext.loop.tournament_helpers import resolve_gate_decision

        result = resolve_gate_decision(
            tournament_best_score=0.8,
            previous_best=0.7,
            gate=None,
            score_history=[0.5, 0.6, 0.7],
            gate_decision_history=["advance", "advance"],
            retry_count=0,
            max_retries=3,
            use_rapid=True,
            custom_metrics=None,
        )
        assert result.decision == "advance"
        assert result.is_rapid is True

    def test_rapid_rollback_on_no_improvement(self) -> None:
        from autocontext.loop.tournament_helpers import resolve_gate_decision

        result = resolve_gate_decision(
            tournament_best_score=0.65,
            previous_best=0.7,
            gate=None,
            score_history=[0.5, 0.6, 0.7],
            gate_decision_history=["advance", "advance"],
            retry_count=0,
            max_retries=3,
            use_rapid=True,
            custom_metrics=None,
        )
        assert result.decision == "rollback"

    def test_standard_gate_advance(self) -> None:
        from autocontext.harness.pipeline.gate import BackpressureGate
        from autocontext.loop.tournament_helpers import resolve_gate_decision

        gate = BackpressureGate(min_delta=0.005)
        result = resolve_gate_decision(
            tournament_best_score=0.75,
            previous_best=0.70,
            gate=gate,
            score_history=[0.5, 0.6, 0.7],
            gate_decision_history=["advance", "advance"],
            retry_count=0,
            max_retries=3,
            use_rapid=False,
            custom_metrics=None,
        )
        assert result.decision == "advance"
        assert result.delta > 0

    def test_standard_gate_retry(self) -> None:
        from autocontext.harness.pipeline.gate import BackpressureGate
        from autocontext.loop.tournament_helpers import resolve_gate_decision

        gate = BackpressureGate(min_delta=0.1)
        result = resolve_gate_decision(
            tournament_best_score=0.71,
            previous_best=0.70,
            gate=gate,
            score_history=[0.7],
            gate_decision_history=["advance"],
            retry_count=0,
            max_retries=3,
            use_rapid=False,
            custom_metrics=None,
        )
        assert result.decision == "retry"

    def test_standard_gate_rollback_max_retries(self) -> None:
        from autocontext.harness.pipeline.gate import BackpressureGate
        from autocontext.loop.tournament_helpers import resolve_gate_decision

        gate = BackpressureGate(min_delta=0.1)
        result = resolve_gate_decision(
            tournament_best_score=0.71,
            previous_best=0.70,
            gate=gate,
            score_history=[0.7],
            gate_decision_history=["advance"],
            retry_count=3,
            max_retries=3,
            use_rapid=False,
            custom_metrics=None,
        )
        # At max retries, gate evaluates but runner should cap to rollback
        assert result.decision in ("retry", "rollback")

    def test_trend_aware_gate(self) -> None:
        from autocontext.harness.pipeline.trend_gate import TrendAwareGate
        from autocontext.loop.tournament_helpers import resolve_gate_decision

        gate = TrendAwareGate(min_delta=0.005)
        result = resolve_gate_decision(
            tournament_best_score=0.75,
            previous_best=0.70,
            gate=gate,
            score_history=[0.5, 0.6, 0.7],
            gate_decision_history=["advance", "advance", "advance"],
            retry_count=0,
            max_retries=3,
            use_rapid=False,
            custom_metrics={},
        )
        assert result.decision == "advance"
        assert result.is_rapid is False


# ===========================================================================
# build_retry_prompt
# ===========================================================================


class TestBuildRetryPrompt:
    def test_includes_attempt_and_score(self) -> None:
        from autocontext.loop.tournament_helpers import build_retry_prompt

        prompt = build_retry_prompt(
            base_prompt="You are the competitor.",
            tournament_best_score=0.65,
            previous_best=0.70,
            min_delta=0.005,
            current_strategy={"aggression": 0.8},
            attempt=2,
            is_code_strategy=False,
        )
        assert "RETRY ATTEMPT 2" in prompt
        assert "0.6500" in prompt
        assert "0.7000" in prompt

    def test_includes_strategy_json_for_non_code(self) -> None:
        from autocontext.loop.tournament_helpers import build_retry_prompt

        prompt = build_retry_prompt(
            base_prompt="You are the competitor.",
            tournament_best_score=0.5,
            previous_best=0.6,
            min_delta=0.005,
            current_strategy={"defense": 0.9},
            attempt=1,
            is_code_strategy=False,
        )
        assert '"defense"' in prompt
        assert "Adjust your strategy" in prompt

    def test_code_strategy_mode(self) -> None:
        from autocontext.loop.tournament_helpers import build_retry_prompt

        prompt = build_retry_prompt(
            base_prompt="You are the competitor.",
            tournament_best_score=0.5,
            previous_best=0.6,
            min_delta=0.005,
            current_strategy={"__code__": "result = {}"},
            attempt=1,
            is_code_strategy=True,
        )
        assert "Adjust your code" in prompt
        assert "Do not repeat the same approach" in prompt

    def test_code_strategy_suffix_can_be_forced_without_interface_text(self) -> None:
        from autocontext.loop.tournament_helpers import build_retry_prompt

        prompt = build_retry_prompt(
            base_prompt="You are the competitor.",
            tournament_best_score=0.5,
            previous_best=0.6,
            min_delta=0.005,
            current_strategy={"__code__": "result = {}"},
            attempt=1,
            is_code_strategy=True,
            include_code_strategy_suffix=True,
            strategy_interface="",
        )
        assert "CODE STRATEGY MODE" in prompt

    def test_includes_failure_report(self) -> None:
        from autocontext.loop.tournament_helpers import build_retry_prompt

        prompt = build_retry_prompt(
            base_prompt="Base prompt.",
            tournament_best_score=0.4,
            previous_best=0.6,
            min_delta=0.005,
            current_strategy={"aggression": 0.5},
            attempt=1,
            is_code_strategy=False,
            failure_report_context="Match 1: lost due to poor defense",
        )
        assert "Match 1: lost due to poor defense" in prompt

    def test_empty_failure_report_ok(self) -> None:
        from autocontext.loop.tournament_helpers import build_retry_prompt

        prompt = build_retry_prompt(
            base_prompt="Base.",
            tournament_best_score=0.5,
            previous_best=0.6,
            min_delta=0.005,
            current_strategy={},
            attempt=1,
            is_code_strategy=False,
            failure_report_context="",
        )
        assert "RETRY ATTEMPT" in prompt


# ===========================================================================
# apply_tournament_outcome
# ===========================================================================


class TestApplyTournamentOutcome:
    def test_advance_updates_previous_best(self) -> None:
        from autocontext.loop.tournament_helpers import apply_tournament_outcome

        tournament = _make_tournament(best_score=0.85, elo_after=1100.0)
        result = apply_tournament_outcome(
            gate_decision="advance",
            tournament=tournament,
            previous_best=0.70,
            challenger_elo=1000.0,
            score_history=[0.5, 0.6, 0.7],
            gate_decision_history=["advance", "advance"],
        )
        assert result["previous_best"] == 0.85
        assert result["challenger_elo"] == 1100.0
        assert result["gate_delta"] > 0
        assert 0.85 in result["score_history"]

    def test_rollback_preserves_previous_best(self) -> None:
        from autocontext.loop.tournament_helpers import apply_tournament_outcome

        tournament = _make_tournament(best_score=0.65)
        result = apply_tournament_outcome(
            gate_decision="rollback",
            tournament=tournament,
            previous_best=0.70,
            challenger_elo=1000.0,
            score_history=[0.7],
            gate_decision_history=["advance"],
        )
        assert result["previous_best"] == 0.70
        assert result["challenger_elo"] == 1000.0

    def test_retry_preserves_previous_best(self) -> None:
        from autocontext.loop.tournament_helpers import apply_tournament_outcome

        tournament = _make_tournament(best_score=0.71)
        result = apply_tournament_outcome(
            gate_decision="retry",
            tournament=tournament,
            previous_best=0.70,
            challenger_elo=1000.0,
            score_history=[],
            gate_decision_history=[],
        )
        assert result["previous_best"] == 0.70

    def test_appends_to_histories(self) -> None:
        from autocontext.loop.tournament_helpers import apply_tournament_outcome

        tournament = _make_tournament(best_score=0.8)
        result = apply_tournament_outcome(
            gate_decision="advance",
            tournament=tournament,
            previous_best=0.7,
            challenger_elo=1000.0,
            score_history=[0.5, 0.6, 0.7],
            gate_decision_history=["advance", "advance", "advance"],
        )
        assert len(result["score_history"]) == 4
        assert result["score_history"][-1] == 0.8
        assert len(result["gate_decision_history"]) == 4
        assert result["gate_decision_history"][-1] == "advance"

    def test_gate_delta_computed(self) -> None:
        from autocontext.loop.tournament_helpers import apply_tournament_outcome

        tournament = _make_tournament(best_score=0.75)
        result = apply_tournament_outcome(
            gate_decision="advance",
            tournament=tournament,
            previous_best=0.70,
            challenger_elo=1000.0,
            score_history=[],
            gate_decision_history=[],
        )
        assert result["gate_delta"] == round(0.75 - 0.70, 6)


# ===========================================================================
# build_validity_rollback
# ===========================================================================


class TestBuildValidityRollback:
    def test_returns_rollback_state(self) -> None:
        tournament = _make_tournament(best_score=0.0, mean_score=0.0, wins=0, losses=0)
        from autocontext.loop.tournament_helpers import build_validity_rollback

        result = build_validity_rollback(
            current_strategy={"aggression": 0.5},
            validity_retry_attempts=3,
            score_history=[0.4, 0.5],
            gate_decision_history=["advance", "retry"],
            tournament=tournament,
        )
        assert result["gate_decision"] == "rollback"
        assert result["gate_delta"] == 0.0
        assert result["attempt"] == 3
        assert result["current_strategy"] == {"aggression": 0.5}
        assert result["score_history"] == [0.4, 0.5, 0.0]
        assert result["gate_decision_history"] == ["advance", "retry", "rollback"]
        assert result["tournament"] is tournament

    def test_score_zero(self) -> None:
        tournament = _make_tournament(best_score=0.0, mean_score=0.0, wins=0, losses=0)
        from autocontext.loop.tournament_helpers import build_validity_rollback

        result = build_validity_rollback(
            current_strategy={},
            validity_retry_attempts=0,
            score_history=[],
            gate_decision_history=[],
            tournament=tournament,
        )
        assert result["score"] == 0.0
