"""SampleStateGenerator — produces diverse game states for harness testing.

Simulates random play from a ScenarioInterface to collect states uniformly
across early, mid, and late game phases.  When the scenario supports
``enumerate_legal_actions()``, ground-truth legal actions are attached to
each sample.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any

from autocontext.scenarios.base import ScenarioInterface

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SampleState:
    """A sampled game state with metadata for harness testing."""

    state: dict[str, Any]
    description: str
    expected_legal_actions: list[dict[str, Any]] | None
    difficulty: str  # "early", "mid", "late"


def _classify_phase(turn: int, max_turn: int) -> str:
    """Map a turn index to a game phase label."""
    if max_turn <= 0:
        return "early"
    ratio = turn / max_turn
    if ratio < 0.33:
        return "early"
    if ratio < 0.66:
        return "mid"
    return "late"


class SampleStateGenerator:
    """Generate diverse game states by simulating random play.

    Parameters
    ----------
    scenario:
        Any ``ScenarioInterface`` implementation.
    n_states:
        Minimum number of states to produce (default 50).
    """

    def __init__(self, scenario: ScenarioInterface, n_states: int = 50) -> None:
        self._scenario = scenario
        self._n_states = n_states
        self._cache: list[SampleState] | None = None
        self._cache_ground_truth: list[SampleState] | None = None

    # ── public API ────────────────────────────────────────────────────────

    def generate(self) -> list[SampleState]:
        """Generate diverse sample states **without** ground-truth legal actions."""
        if self._cache is not None:
            return self._cache
        self._cache = self._collect_states(include_ground_truth=False)
        return self._cache

    def generate_with_ground_truth(self) -> list[SampleState]:
        """Generate diverse sample states **with** ground-truth legal actions.

        If the scenario does not support ``enumerate_legal_actions()``, the
        ``expected_legal_actions`` field will be ``None`` on every sample.
        """
        if self._cache_ground_truth is not None:
            return self._cache_ground_truth
        self._cache_ground_truth = self._collect_states(include_ground_truth=True)
        return self._cache_ground_truth

    # ── internals ─────────────────────────────────────────────────────────

    def _collect_states(self, *, include_ground_truth: bool) -> list[SampleState]:
        """Run multiple random games and sample states across phases."""
        collected: list[SampleState] = []
        phase_counts: dict[str, int] = {"early": 0, "mid": 0, "late": 0}
        target_per_phase = max(1, self._n_states // 3)

        # Run games with different seeds until we have enough states
        seed = 0
        max_games = self._n_states * 10  # safety cap
        games_played = 0

        while len(collected) < self._n_states and games_played < max_games:
            game_states = self._simulate_game(seed)
            if not game_states:
                seed += 1
                games_played += 1
                continue

            max_turn = len(game_states) - 1 if len(game_states) > 1 else 1

            for turn_idx, state in enumerate(game_states):
                phase = _classify_phase(turn_idx, max_turn)

                # Prefer under-represented phases
                if phase_counts[phase] >= target_per_phase and len(collected) >= self._n_states:
                    continue

                legal_actions: list[dict[str, Any]] | None = None
                if include_ground_truth:
                    legal_actions = self._scenario.enumerate_legal_actions(state)

                description = f"Turn {turn_idx}/{max_turn} (seed={seed}, phase={phase})"
                sample = SampleState(
                    state=dict(state),
                    description=description,
                    expected_legal_actions=legal_actions,
                    difficulty=phase,
                )
                collected.append(sample)
                phase_counts[phase] += 1

            seed += 1
            games_played += 1

        return collected

    def _simulate_game(self, seed: int) -> list[dict[str, Any]]:
        """Play a random game from initial state, returning all intermediate states."""
        rng = random.Random(seed)
        try:
            state = self._scenario.initial_state(seed=seed)
        except Exception:
            LOGGER.debug("initial_state failed for seed %d", seed, exc_info=True)
            return []

        states: list[dict[str, Any]] = [dict(state)]
        max_steps = 200  # safety cap

        for _ in range(max_steps):
            if self._scenario.is_terminal(state):
                break

            actions = self._random_actions(state, rng)
            if actions is None:
                LOGGER.debug("no valid actions generated during simulation for seed %d", seed)
                break
            try:
                state = self._scenario.step(state, actions)
            except Exception:
                LOGGER.debug("step failed during simulation", exc_info=True)
                break

            states.append(dict(state))

        return states

    def _random_actions(self, state: dict[str, Any], rng: random.Random) -> dict[str, Any] | None:
        """Generate random valid actions for the current state."""
        legal = self._scenario.enumerate_legal_actions(state)
        if legal is not None and len(legal) > 0:
            # Check if continuous parameter space
            if all(a.get("type") == "continuous" and "range" in a for a in legal):
                baseline: dict[str, Any] = {}
                for a in legal:
                    low, _ = a["range"]
                    baseline[str(a["action"])] = round(float(low), 4)
                if self._is_valid_action(state, baseline):
                    return baseline

                for _ in range(32):
                    actions: dict[str, Any] = {}
                    for a in legal:
                        low, high = a["range"]
                        actions[str(a["action"])] = round(rng.uniform(float(low), float(high)), 4)
                    if self._is_valid_action(state, actions):
                        return actions
                return None
            # Discrete: pick one
            choices = list(legal)
            rng.shuffle(choices)
            for choice in choices:
                candidate = dict(choice)
                if self._is_valid_action(state, candidate):
                    return candidate
            return None

        # Fallback: try a simple strategy with random values
        candidate = {"value": round(rng.random(), 4)}
        if self._is_valid_action(state, candidate):
            return candidate
        return None

    def _is_valid_action(self, state: dict[str, Any], actions: dict[str, Any]) -> bool:
        """Return whether the scenario accepts the candidate action mapping."""
        try:
            valid, _ = self._scenario.validate_actions(state, "generator", actions)
        except Exception:
            LOGGER.debug("validate_actions failed during sampling", exc_info=True)
            return False
        return valid
