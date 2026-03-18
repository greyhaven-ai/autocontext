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
    def size(self) -> int:
        return len(self._opponents)


def build_opponent_pool(
    baselines: list[dict[str, Any]],
    self_play_pool: SelfPlayPool,
) -> list[dict[str, Any]]:
    """Build combined opponent pool from baselines and self-play.

    Each entry is a dict with at minimum a "strategy" key.
    Self-play entries are tagged with "source": "self_play".
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

    return pool
