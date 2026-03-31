"""Pluggable scoring backends with uncertainty-aware alternatives to Elo (AC-319).

Makes scoring/ranking backends pluggable. Elo remains the default baseline.
Glicko-style backend adds uncertainty tracking so early noisy candidates
get appropriate confidence.

Key types:
- TrialResult: preserves continuous trial score (not just win/loss)
- RatingUpdate: rating change with optional uncertainty
- ScoringBackend: abstract interface
- EloBackend: classical Elo (default)
- GlickoBackend: simplified Glicko with uncertainty decay
- get_backend(): factory by name
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

_WIN_THRESHOLD = 0.55
_ELO_K = 32.0
_GLICKO_Q = math.log(10) / 400


def _normalize_score(score: float) -> float:
    return max(0.0, min(1.0, float(score)))


@dataclass(slots=True)
class TrialResult:
    """A single trial preserving the continuous score."""

    score: float
    seed: int
    opponent_rating: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_win(self, threshold: float = _WIN_THRESHOLD) -> bool:
        return self.score >= threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "seed": self.seed,
            "opponent_rating": self.opponent_rating,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrialResult:
        return cls(
            score=data.get("score", 0.0),
            seed=data.get("seed", 0),
            opponent_rating=data.get("opponent_rating", 1000.0),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class RatingUpdate:
    """Result of a scoring backend update."""

    rating_before: float
    rating_after: float
    uncertainty_before: float | None
    uncertainty_after: float | None
    backend_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ScoringBackend(ABC):
    """Abstract scoring/ranking backend."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend identifier."""

    @property
    def default_uncertainty(self) -> float | None:
        """Initial uncertainty for backends that track it."""
        return None

    @abstractmethod
    def update(
        self,
        current_rating: float,
        trials: Sequence[TrialResult],
        uncertainty: float | None = None,
    ) -> RatingUpdate:
        """Compute rating update from trial results."""


class EloBackend(ScoringBackend):
    """Classical Elo rating (default baseline)."""

    def __init__(self, k_factor: float = _ELO_K) -> None:
        self._k = k_factor

    @property
    def name(self) -> str:
        return "elo"

    def update(
        self,
        current_rating: float,
        trials: Sequence[TrialResult],
        uncertainty: float | None = None,
    ) -> RatingUpdate:
        rating = current_rating
        trial_scores: list[float] = []

        for trial in trials:
            expected = 1.0 / (1.0 + 10 ** ((trial.opponent_rating - rating) / 400))
            actual = _normalize_score(trial.score)
            rating += self._k * (actual - expected)
            trial_scores.append(trial.score)

        return RatingUpdate(
            rating_before=current_rating,
            rating_after=round(rating, 2),
            uncertainty_before=None,
            uncertainty_after=None,
            backend_name=self.name,
            metadata={"trial_scores": trial_scores, "k_factor": self._k},
        )


class GlickoBackend(ScoringBackend):
    """Simplified Glicko-style backend with uncertainty tracking."""

    def __init__(self, default_rd: float = 350.0) -> None:
        self._default_rd = default_rd

    @property
    def name(self) -> str:
        return "glicko"

    @property
    def default_uncertainty(self) -> float | None:
        return self._default_rd

    def update(
        self,
        current_rating: float,
        trials: Sequence[TrialResult],
        uncertainty: float | None = None,
    ) -> RatingUpdate:
        rd = uncertainty if uncertainty is not None else self._default_rd
        if not trials:
            return RatingUpdate(
                rating_before=current_rating,
                rating_after=current_rating,
                uncertainty_before=rd,
                uncertainty_after=rd,
                backend_name=self.name,
            )

        # Simplified Glicko update
        q = _GLICKO_Q
        d_sq_inv = 0.0
        score_sum = 0.0

        for trial in trials:
            g_rd = 1.0 / math.sqrt(1.0 + 3.0 * q * q * (200.0 ** 2) / (math.pi ** 2))
            e = 1.0 / (1.0 + 10 ** (-g_rd * (current_rating - trial.opponent_rating) / 400))
            d_sq_inv += q * q * g_rd * g_rd * e * (1 - e)
            actual = _normalize_score(trial.score)
            score_sum += g_rd * (actual - e)

        d_sq = 1.0 / max(d_sq_inv, 1e-10)
        new_rd_sq = 1.0 / (1.0 / (rd * rd) + 1.0 / d_sq)
        new_rd = math.sqrt(new_rd_sq)
        new_rating = current_rating + q * new_rd_sq * score_sum

        return RatingUpdate(
            rating_before=current_rating,
            rating_after=round(new_rating, 2),
            uncertainty_before=round(rd, 2),
            uncertainty_after=round(new_rd, 2),
            backend_name=self.name,
            metadata={
                "trial_scores": [t.score for t in trials],
                "d_squared": round(d_sq, 2),
            },
        )


def get_backend(name: str) -> ScoringBackend:
    """Get scoring backend by name. Falls back to Elo for unknown names."""
    backends: dict[str, ScoringBackend] = {
        "elo": EloBackend(),
        "glicko": GlickoBackend(),
    }
    return backends.get(name, EloBackend())
