"""Tests for TournamentEvalAdapter — EvaluationRunner-backed tournament producing TournamentSummary."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from mts.execution.supervisor import ExecutionOutput, ExecutionSupervisor
from mts.execution.tournament import TournamentRunner, TournamentSummary
from mts.scenarios.base import (
    ExecutionLimits,
    Observation,
    ReplayEnvelope,
    Result,
    ScenarioInterface,
)

# ---------------------------------------------------------------------------
# Fake implementations
# ---------------------------------------------------------------------------


class FakeScenario(ScenarioInterface):
    """Deterministic scenario that derives score from strategy 'aggression' param and seed."""

    name = "fake_scenario"

    def describe_rules(self) -> str:
        return "Fake scenario for testing."

    def describe_strategy_interface(self) -> str:
        return '{"aggression": float}'

    def describe_evaluation_criteria(self) -> str:
        return "Score is derived from aggression parameter."

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "terminal": False}

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        return Observation(narrative="test observation")

    def validate_actions(
        self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any]
    ) -> tuple[bool, str]:
        return (True, "")

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        aggression = float(actions.get("aggression", 0.5))
        seed = state.get("seed", 0)
        # Deterministic score: aggression * (1 + seed % 5) / 5, capped at 1.0
        score = min(1.0, aggression * (1 + seed % 5) / 5)
        return {"seed": seed, "terminal": True, "score": score}

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return state.get("terminal", False)

    def get_result(self, state: Mapping[str, Any]) -> Result:
        score = state.get("score", 0.5)
        return Result(score=score, summary="test", replay=[])

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return "test narrative"

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {"state": dict(state)}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTournamentEvalAdapter:
    """Test suite for TournamentEvalAdapter."""

    def _make_supervisor(self) -> ExecutionSupervisor:
        """Create a supervisor using a simple inline executor wrapping execute_match."""

        class InlineExecutor:
            def execute(
                self,
                scenario: ScenarioInterface,
                strategy: Mapping[str, Any],
                seed: int,
                limits: ExecutionLimits,
            ) -> tuple[Result, ReplayEnvelope]:
                result = scenario.execute_match(strategy=strategy, seed=seed)
                replay = ReplayEnvelope(
                    scenario=scenario.name,
                    seed=seed,
                    narrative=scenario.replay_to_narrative(result.replay),
                    timeline=result.replay,
                )
                return result, replay

        return ExecutionSupervisor(executor=InlineExecutor())

    def test_returns_tournament_summary(self) -> None:
        """Adapter.run() returns a TournamentSummary instance."""
        from mts.execution.eval_adapter import TournamentEvalAdapter

        supervisor = self._make_supervisor()
        adapter = TournamentEvalAdapter(supervisor=supervisor)
        result = adapter.run(
            scenario=FakeScenario(),
            strategy={"aggression": 0.7},
            seed_base=0,
            matches=3,
            limits=ExecutionLimits(),
            challenger_elo=1000.0,
        )
        assert isinstance(result, TournamentSummary)

    def test_scores_match_direct_tournament(self) -> None:
        """Adapter produces the same mean/best scores as TournamentRunner with identical inputs."""
        from mts.execution.eval_adapter import TournamentEvalAdapter

        supervisor = self._make_supervisor()
        scenario = FakeScenario()
        strategy = {"aggression": 0.8}
        seed_base = 10
        matches = 5
        limits = ExecutionLimits()
        challenger_elo = 1000.0

        # Run via direct TournamentRunner
        direct = TournamentRunner(supervisor=supervisor, opponent_elo=1000.0)
        direct_summary = direct.run(
            scenario=scenario,
            strategy=strategy,
            seed_base=seed_base,
            matches=matches,
            limits=limits,
            challenger_elo=challenger_elo,
        )

        # Run via adapter
        adapter = TournamentEvalAdapter(supervisor=supervisor, opponent_elo=1000.0)
        adapter_summary = adapter.run(
            scenario=scenario,
            strategy=strategy,
            seed_base=seed_base,
            matches=matches,
            limits=limits,
            challenger_elo=challenger_elo,
        )

        assert adapter_summary.mean_score == pytest.approx(direct_summary.mean_score)
        assert adapter_summary.best_score == pytest.approx(direct_summary.best_score)
        assert adapter_summary.wins == direct_summary.wins
        assert adapter_summary.losses == direct_summary.losses

    def test_elo_updates_match(self) -> None:
        """Elo changes from the initial challenger value after matches."""
        from mts.execution.eval_adapter import TournamentEvalAdapter

        supervisor = self._make_supervisor()
        adapter = TournamentEvalAdapter(supervisor=supervisor)

        # High aggression -> scores above 0.55 threshold -> wins -> elo should increase
        result = adapter.run(
            scenario=FakeScenario(),
            strategy={"aggression": 0.9},
            seed_base=1,
            matches=3,
            limits=ExecutionLimits(),
            challenger_elo=1000.0,
        )
        assert result.elo_after != 1000.0

    def test_outputs_contain_execution_outputs(self) -> None:
        """The outputs list contains exactly `matches` ExecutionOutput instances."""
        from mts.execution.eval_adapter import TournamentEvalAdapter

        supervisor = self._make_supervisor()
        adapter = TournamentEvalAdapter(supervisor=supervisor)
        matches = 4
        result = adapter.run(
            scenario=FakeScenario(),
            strategy={"aggression": 0.6},
            seed_base=0,
            matches=matches,
            limits=ExecutionLimits(),
            challenger_elo=1000.0,
        )
        assert len(result.outputs) == matches
        assert all(isinstance(o, ExecutionOutput) for o in result.outputs)

    def test_on_match_callback(self) -> None:
        """The on_match callback is invoked once per match with (idx, score)."""
        from mts.execution.eval_adapter import TournamentEvalAdapter

        supervisor = self._make_supervisor()
        adapter = TournamentEvalAdapter(supervisor=supervisor)
        callback_log: list[tuple[int, float]] = []

        def on_match(idx: int, score: float) -> None:
            callback_log.append((idx, score))

        adapter.run(
            scenario=FakeScenario(),
            strategy={"aggression": 0.5},
            seed_base=0,
            matches=3,
            limits=ExecutionLimits(),
            challenger_elo=1000.0,
            on_match=on_match,
        )
        assert len(callback_log) == 3
        # Check indices are sequential
        assert [c[0] for c in callback_log] == [0, 1, 2]
        # Each score should be a float
        assert all(isinstance(c[1], float) for c in callback_log)

    def test_custom_opponent_elo(self) -> None:
        """Custom opponent_elo constructor param affects Elo calculations."""
        from mts.execution.eval_adapter import TournamentEvalAdapter

        supervisor = self._make_supervisor()

        # With opponent elo=500 (easy) — winning should give smaller elo gain
        adapter_easy = TournamentEvalAdapter(supervisor=supervisor, opponent_elo=500.0)
        result_easy = adapter_easy.run(
            scenario=FakeScenario(),
            strategy={"aggression": 0.9},
            seed_base=1,
            matches=3,
            limits=ExecutionLimits(),
            challenger_elo=1000.0,
        )

        # With opponent elo=1500 (hard) — winning should give bigger elo gain
        adapter_hard = TournamentEvalAdapter(supervisor=supervisor, opponent_elo=1500.0)
        result_hard = adapter_hard.run(
            scenario=FakeScenario(),
            strategy={"aggression": 0.9},
            seed_base=1,
            matches=3,
            limits=ExecutionLimits(),
            challenger_elo=1000.0,
        )

        # Both should produce same scores (same scenario/strategy/seeds)
        assert result_easy.mean_score == pytest.approx(result_hard.mean_score)
        # But elo should differ — beating harder opponent gives more elo
        assert result_hard.elo_after > result_easy.elo_after
