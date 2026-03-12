"""Tests for PolicyExecutor — zero-LLM match execution of code policies."""
from __future__ import annotations

import textwrap
from typing import Any

import pytest

from autocontext.execution.policy_executor import PolicyExecutor, PolicyMatchResult
from autocontext.scenarios.base import Result
from autocontext.scenarios.grid_ctf import GridCtfScenario
from autocontext.scenarios.othello import OthelloScenario

# ── PolicyMatchResult dataclass ───────────────────────────────────────────────


class TestPolicyMatchResult:
    def test_frozen_dataclass(self) -> None:
        r = PolicyMatchResult(
            score=0.5,
            normalized_score=0.5,
            had_illegal_actions=False,
            illegal_action_count=0,
            errors=[],
            moves_played=1,
            replay=None,
        )
        assert r.score == 0.5
        assert r.normalized_score == 0.5
        assert r.had_illegal_actions is False
        assert r.illegal_action_count == 0
        assert r.errors == []
        assert r.moves_played == 1
        assert r.replay is None

    def test_frozen_immutable(self) -> None:
        r = PolicyMatchResult(
            score=0.5,
            normalized_score=0.5,
            had_illegal_actions=False,
            illegal_action_count=0,
            errors=[],
            moves_played=1,
            replay=None,
        )
        with pytest.raises(AttributeError):
            r.score = 1.0  # type: ignore[misc]


# ── PolicyExecutor construction ───────────────────────────────────────────────


class TestPolicyExecutorInit:
    def test_creates_with_scenario(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        assert executor is not None

    def test_creates_with_custom_timeout(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario, timeout_per_match=10.0)
        assert executor is not None

    def test_creates_with_safe_builtins_false(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario, safe_builtins=False)
        assert executor is not None


# ── AST safety checks ────────────────────────────────────────────────────────


class TestPolicyExecutorSafety:
    def test_rejects_import_statements(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            import os
            def choose_action(state):
                return {"aggression": 0.5, "defense": 0.3, "path_bias": 0.6}
        """)
        result = executor.execute_match(policy, seed=42)
        assert len(result.errors) > 0
        assert any("import" in e.lower() or "safety" in e.lower() for e in result.errors)
        assert result.score == 0.0

    def test_rejects_dangerous_builtins(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return globals()
        """)
        result = executor.execute_match(policy, seed=42)
        assert len(result.errors) > 0
        assert result.score == 0.0

    def test_rejects_open(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                f = open('/etc/passwd')
                return {"aggression": 0.5, "defense": 0.3, "path_bias": 0.6}
        """)
        result = executor.execute_match(policy, seed=42)
        assert len(result.errors) > 0
        assert result.score == 0.0

    def test_rejects_dunder_access(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                x = ().__class__.__bases__[0].__subclasses__()
                return {"aggression": 0.5, "defense": 0.3, "path_bias": 0.6}
        """)
        result = executor.execute_match(policy, seed=42)
        assert len(result.errors) > 0
        assert result.score == 0.0

    def test_rejects_syntax_error(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = "def choose_action(state:\n"
        result = executor.execute_match(policy, seed=42)
        assert len(result.errors) > 0
        assert result.score == 0.0


# ── Restricted builtins ───────────────────────────────────────────────────────


class TestPolicyExecutorBuiltins:
    def test_allows_math_operations(self) -> None:
        """Policies should have access to math-like builtins."""
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                a = max(0.3, min(0.8, 0.5))
                d = abs(0.3 - 0.1)
                return {"aggression": a, "defense": d, "path_bias": 0.6}
        """)
        result = executor.execute_match(policy, seed=42)
        assert result.errors == []
        assert result.score > 0.0

    def test_safe_builtins_provides_expected_functions(self) -> None:
        """Basic safe builtins like len, range, sorted, etc. should be available."""
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                items = list(range(5))
                n = len(items)
                s = sorted(items, reverse=True)
                total = sum(s)
                return {"aggression": 0.5, "defense": 0.3, "path_bias": 0.6}
        """)
        result = executor.execute_match(policy, seed=42)
        assert result.errors == []


# ── Execution with grid_ctf ──────────────────────────────────────────────────


class TestPolicyExecutorGridCtf:
    def test_valid_policy_returns_score(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"aggression": 0.7, "defense": 0.5, "path_bias": 0.8}
        """)
        result = executor.execute_match(policy, seed=42)
        assert result.score > 0.0
        assert 0.0 <= result.normalized_score <= 1.0
        assert result.had_illegal_actions is False
        assert result.illegal_action_count == 0
        assert result.errors == []
        assert result.moves_played >= 1

    def test_deterministic_with_same_seed(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"aggression": 0.6, "defense": 0.4, "path_bias": 0.7}
        """)
        r1 = executor.execute_match(policy, seed=42)
        r2 = executor.execute_match(policy, seed=42)
        assert r1.score == r2.score

    def test_different_seeds_may_differ(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"aggression": 0.6, "defense": 0.4, "path_bias": 0.7}
        """)
        r1 = executor.execute_match(policy, seed=1)
        r2 = executor.execute_match(policy, seed=99)
        # Scores may differ due to stochastic noise in scenario
        # (but we can't assert they *must* differ — just that both execute)
        assert r1.score > 0.0
        assert r2.score > 0.0

    def test_state_aware_policy(self) -> None:
        """A policy that reads scenario state and adjusts accordingly."""
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                enemy_bias = state.get("enemy_spawn_bias", 0.5)
                resource = state.get("resource_density", 0.5)
                aggression = 0.8 if resource > 0.5 else 0.4
                defense = 0.6 if enemy_bias > 0.5 else 0.3
                return {"aggression": aggression, "defense": defense, "path_bias": 0.6}
        """)
        result = executor.execute_match(policy, seed=42)
        assert result.errors == []
        assert result.score > 0.0

    def test_illegal_action_detected(self) -> None:
        """Policy returning invalid actions should have had_illegal_actions=True."""
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        # aggression + defense > 1.4 is the constraint violation
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"aggression": 1.0, "defense": 1.0, "path_bias": 0.5}
        """)
        result = executor.execute_match(policy, seed=42)
        assert result.had_illegal_actions is True
        assert result.illegal_action_count >= 1

    def test_missing_fields_detected(self) -> None:
        """Policy returning incomplete action dict should be detected."""
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"aggression": 0.5}
        """)
        result = executor.execute_match(policy, seed=42)
        assert result.had_illegal_actions is True
        assert result.illegal_action_count >= 1

    def test_replay_populated(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"aggression": 0.5, "defense": 0.3, "path_bias": 0.7}
        """)
        result = executor.execute_match(policy, seed=42)
        assert result.replay is not None

    def test_multi_turn_policy_runs_until_terminal(self) -> None:
        """Policies should be invoked once per turn until the scenario ends."""

        class _MultiTurnScenario:
            name = "multi_turn"

            def describe_rules(self) -> str:
                return "Reach turn 3."

            def describe_strategy_interface(self) -> str:
                return "{move: int}"

            def describe_evaluation_criteria(self) -> str:
                return "Higher score is better."

            def initial_state(self, seed: int | None = None) -> dict[str, Any]:
                return {"seed": seed or 0, "turn": 0, "terminal": False}

            def get_observation(self, state: dict[str, Any], player_id: str) -> Any:
                raise NotImplementedError

            def validate_actions(self, state: dict[str, Any], player_id: str, actions: dict[str, Any]) -> tuple[bool, str]:
                return ("move" in actions, "missing move" if "move" not in actions else "ok")

            def step(self, state: dict[str, Any], actions: dict[str, Any]) -> dict[str, Any]:
                next_turn = state["turn"] + 1
                return {
                    **state,
                    "turn": next_turn,
                    "terminal": next_turn >= 3,
                    "timeline": [*state.get("timeline", []), {"turn": next_turn, "move": actions["move"]}],
                    "score": 0.25 * next_turn,
                }

            def is_terminal(self, state: dict[str, Any]) -> bool:
                return bool(state["terminal"])

            def get_result(self, state: dict[str, Any]) -> Result:
                return Result(
                    score=float(state["score"]),
                    winner="challenger",
                    summary="done",
                    replay=list(state.get("timeline", [])),
                    metrics={"turns": float(state["turn"])},
                    validation_errors=[],
                )

            def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
                return "multi turn"

            def render_frame(self, state: dict[str, Any]) -> dict[str, Any]:
                return state

        scenario = _MultiTurnScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"move": state["turn"] + 1}
        """)

        result = executor.execute_match(policy, seed=42)

        assert result.score == 0.75
        assert result.moves_played == 3
        assert result.errors == []

    def test_non_terminal_policy_is_stopped_by_move_budget(self) -> None:
        """A scenario that never terminates should hit the executor move budget."""

        class _LoopScenario:
            name = "loop"

            def describe_rules(self) -> str:
                return "Never terminates."

            def describe_strategy_interface(self) -> str:
                return "{move: int}"

            def describe_evaluation_criteria(self) -> str:
                return "n/a"

            def initial_state(self, seed: int | None = None) -> dict[str, Any]:
                return {"seed": seed or 0, "turn": 0, "terminal": False}

            def get_observation(self, state: dict[str, Any], player_id: str) -> Any:
                raise NotImplementedError

            def validate_actions(self, state: dict[str, Any], player_id: str, actions: dict[str, Any]) -> tuple[bool, str]:
                return True, "ok"

            def step(self, state: dict[str, Any], actions: dict[str, Any]) -> dict[str, Any]:
                return {**state, "turn": state["turn"] + 1}

            def is_terminal(self, state: dict[str, Any]) -> bool:
                return False

            def get_result(self, state: dict[str, Any]) -> Result:
                return Result(score=0.0, winner=None, summary="loop", replay=[], metrics={}, validation_errors=[])

            def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
                return "loop"

            def render_frame(self, state: dict[str, Any]) -> dict[str, Any]:
                return state

        scenario = _LoopScenario()
        executor = PolicyExecutor(scenario, max_moves_per_match=3)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"move": 1}
        """)

        result = executor.execute_match(policy, seed=1)

        assert result.score == 0.0
        assert result.moves_played == 3
        assert any("max moves" in error for error in result.errors)


# ── Execution with othello ────────────────────────────────────────────────────


class TestPolicyExecutorOthello:
    def test_valid_policy_returns_score(self) -> None:
        scenario = OthelloScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"mobility_weight": 0.6, "corner_weight": 0.8, "stability_weight": 0.5}
        """)
        result = executor.execute_match(policy, seed=42)
        assert result.score > 0.0
        assert result.errors == []
        assert result.had_illegal_actions is False

    def test_invalid_othello_policy(self) -> None:
        scenario = OthelloScenario()
        executor = PolicyExecutor(scenario)
        # Missing required fields
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"mobility_weight": 0.6}
        """)
        result = executor.execute_match(policy, seed=42)
        assert result.had_illegal_actions is True


# ── Missing choose_action function ───────────────────────────────────────────


class TestPolicyExecutorMissingFunction:
    def test_no_choose_action(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def some_other_function(state):
                return {"aggression": 0.5, "defense": 0.3, "path_bias": 0.7}
        """)
        result = executor.execute_match(policy, seed=42)
        assert len(result.errors) > 0
        assert result.score == 0.0

    def test_choose_action_raises(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                raise ValueError("intentional error")
        """)
        result = executor.execute_match(policy, seed=42)
        assert len(result.errors) > 0
        assert result.score == 0.0

    def test_choose_action_returns_non_dict(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return "not a dict"
        """)
        result = executor.execute_match(policy, seed=42)
        assert result.had_illegal_actions is True


# ── Timeout enforcement ───────────────────────────────────────────────────────


class TestPolicyExecutorTimeout:
    def test_infinite_loop_times_out(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario, timeout_per_match=0.5)
        policy = textwrap.dedent("""\
            def choose_action(state):
                while True:
                    pass
        """)
        result = executor.execute_match(policy, seed=42)
        assert len(result.errors) > 0
        assert any("timeout" in e.lower() or "timed out" in e.lower() for e in result.errors)
        assert result.score == 0.0


# ── Batch execution ──────────────────────────────────────────────────────────


class TestPolicyExecutorBatch:
    def test_batch_multiple_matches(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"aggression": 0.6, "defense": 0.4, "path_bias": 0.7}
        """)
        results = executor.execute_batch(policy, n_matches=3)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, PolicyMatchResult)
            assert r.score > 0.0

    def test_batch_with_explicit_seeds(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"aggression": 0.6, "defense": 0.4, "path_bias": 0.7}
        """)
        results = executor.execute_batch(policy, n_matches=3, seeds=[10, 20, 30])
        assert len(results) == 3
        # With deterministic seeds, re-running should give same results
        results2 = executor.execute_batch(policy, n_matches=3, seeds=[10, 20, 30])
        for r1, r2 in zip(results, results2, strict=True):
            assert r1.score == r2.score

    def test_batch_default_n_matches(self) -> None:
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"aggression": 0.6, "defense": 0.4, "path_bias": 0.7}
        """)
        results = executor.execute_batch(policy)
        assert len(results) == 5  # default n_matches

    def test_batch_seeds_length_mismatch_uses_seeds(self) -> None:
        """When seeds list is provided, n_matches is derived from seeds length."""
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        policy = textwrap.dedent("""\
            def choose_action(state):
                return {"aggression": 0.6, "defense": 0.4, "path_bias": 0.7}
        """)
        results = executor.execute_batch(policy, n_matches=10, seeds=[1, 2])
        assert len(results) == 2  # seeds list takes precedence


# ── Allowed modules (math, collections, re) ──────────────────────────────────


class TestPolicyExecutorAllowedModules:
    def test_math_module_available(self) -> None:
        """Policies should be able to use math functions via injected math module."""
        scenario = GridCtfScenario()
        executor = PolicyExecutor(scenario)
        # math is pre-injected into the namespace, no import needed
        policy = textwrap.dedent("""\
            def choose_action(state):
                a = math.sqrt(0.25)
                return {"aggression": a, "defense": 0.3, "path_bias": 0.7}
        """)
        result = executor.execute_match(policy, seed=42)
        assert result.errors == []
        assert result.score > 0.0
