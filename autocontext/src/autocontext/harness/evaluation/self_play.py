"""Self-play opponent pool for co-evolutionary pressure (AC-334).

Adds previous generation strategies as opponents so the system evolves
against itself instead of only exploiting fixed baselines.

Key types:
- SelfPlayOpponent: a prior strategy with generation and elo
- SelfPlayConfig: enabled, pool_size, weight
- SelfPlayPool: rolling window of top-K prior strategies
- build_opponent_pool(): merges baselines with self-play opponents
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SelfPlayOpponent:
    """A prior generation's strategy used as an opponent."""

    strategy: dict[str, Any]
    generation: int
    elo: float
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "generation": self.generation,
            "elo": self.elo,
            "score": self.score,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelfPlayOpponent:
        return cls(
            strategy=data.get("strategy", {}),
            generation=data.get("generation", 0),
            elo=data.get("elo", 1000.0),
            score=data.get("score", 0.0),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class SelfPlayConfig:
    """Configuration for self-play opponent pool."""

    enabled: bool = False
    pool_size: int = 3
    weight: float = 0.5  # fraction of matches vs self-play opponents

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "pool_size": self.pool_size,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelfPlayConfig:
        return cls(
            enabled=data.get("enabled", False),
            pool_size=data.get("pool_size", 3),
            weight=data.get("weight", 0.5),
        )


class SelfPlayPool:
    """Rolling window of top-K prior strategies as opponents."""

    def __init__(self, config: SelfPlayConfig) -> None:
        self._config = config
        self._opponents: list[SelfPlayOpponent] = []

    def add(self, opponent: SelfPlayOpponent) -> None:
        """Add a new opponent, maintaining pool_size limit."""
        self._opponents.append(opponent)
        if len(self._opponents) > self._config.pool_size:
            # Keep the best by score, breaking ties by recency
            self._opponents.sort(
                key=lambda o: (o.score, o.generation), reverse=True,
            )
            self._opponents = self._opponents[: self._config.pool_size]

    def get_opponents(self) -> list[SelfPlayOpponent]:
        """Return current self-play opponents (empty if disabled)."""
        if not self._config.enabled:
            return []
        return list(self._opponents)

    @property
    def config(self) -> SelfPlayConfig:
        return self._config

    @property
    def size(self) -> int:
        return len(self._opponents)


def planned_self_play_trials(
    trials: int,
    config: SelfPlayConfig,
    *,
    available_opponents: int,
) -> int:
    """Return the number of matches that should use self-play opponents."""
    if not config.enabled or trials <= 0 or available_opponents <= 0:
        return 0
    weight = max(0.0, min(1.0, float(config.weight)))
    if weight <= 0.0:
        return 0
    planned = int(round(trials * weight))
    if planned == 0:
        planned = 1
    return min(trials, planned)


def load_self_play_pool(
    strategy_history: Sequence[dict[str, Any]] | Any,
    config: SelfPlayConfig,
    *,
    current_generation: int,
) -> SelfPlayPool:
    """Build a self-play pool from previously advanced strategies in the run."""
    pool = SelfPlayPool(config)
    if (
        not config.enabled
        or not isinstance(strategy_history, Sequence)
        or isinstance(strategy_history, (str, bytes, bytearray))
    ):
        return pool

    candidates: list[SelfPlayOpponent] = []
    for row in strategy_history:
        if not isinstance(row, dict):
            continue
        generation = row.get("generation_index")
        if not isinstance(generation, int) or generation >= current_generation:
            continue
        if row.get("gate_decision") != "advance":
            continue
        raw_strategy = row.get("content")
        if not isinstance(raw_strategy, str) or not raw_strategy.strip():
            continue
        try:
            strategy = json.loads(raw_strategy)
        except json.JSONDecodeError:
            continue
        if not isinstance(strategy, dict):
            continue
        score = row.get("best_score", 0.0)
        elo = row.get("elo", 1000.0)
        candidates.append(
            SelfPlayOpponent(
                strategy=strategy,
                generation=generation,
                elo=float(elo) if isinstance(elo, (int, float)) else 1000.0,
                score=float(score) if isinstance(score, (int, float)) else 0.0,
                metadata={"gate_decision": "advance"},
            ),
        )

    candidates.sort(key=lambda opponent: (opponent.score, opponent.generation), reverse=True)
    for opponent in candidates:
        pool.add(opponent)
    return pool


def build_opponent_pool(
    baselines: Sequence[dict[str, Any]],
    self_play_pool: SelfPlayPool,
    *,
    trials: int | None = None,
) -> list[dict[str, Any]]:
    """Build an opponent pool or trial schedule from baselines and self-play.

    Baseline entries may be lightweight placeholders such as ``{"source": "baseline"}``.
    Self-play entries always include a concrete ``strategy`` and are tagged with
    ``source="self_play"``.

    When ``trials`` is provided, the returned list is a scheduled match mix whose
    self-play fraction approximates ``SelfPlayConfig.weight``.
    """
    pool: list[dict[str, Any]] = []

    for b in baselines:
        entry = dict(b)
        entry.setdefault("source", "baseline")
        pool.append(entry)

    for opp in self_play_pool.get_opponents():
        pool.append({
            "strategy": opp.strategy,
            "source": "self_play",
            "generation": opp.generation,
            "elo": opp.elo,
            "score": opp.score,
        })

    if trials is None or trials <= 0:
        return pool

    self_play_entries = [entry for entry in pool if entry.get("source") == "self_play"]
    baseline_entries = [entry for entry in pool if entry.get("source") != "self_play"]
    scheduled_self_play = planned_self_play_trials(
        trials,
        self_play_pool.config,
        available_opponents=len(self_play_entries),
    )

    if scheduled_self_play == 0:
        if not baseline_entries:
            return []
        return [dict(baseline_entries[index % len(baseline_entries)]) for index in range(trials)]

    if not baseline_entries:
        return [dict(self_play_entries[index % len(self_play_entries)]) for index in range(trials)]

    scheduled: list[dict[str, Any]] = []
    baseline_index = 0
    self_play_index = 0
    for trial_index in range(trials):
        should_use_self_play = (
            round((trial_index + 1) * scheduled_self_play / trials)
            > round(trial_index * scheduled_self_play / trials)
        )
        if should_use_self_play:
            scheduled.append(dict(self_play_entries[self_play_index % len(self_play_entries)]))
            self_play_index += 1
            continue
        scheduled.append(dict(baseline_entries[baseline_index % len(baseline_entries)]))
        baseline_index += 1
    return scheduled
