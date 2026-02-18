"""Elo rating functions — domain-agnostic scoring primitive."""

from __future__ import annotations


def expected_score(player_rating: float, opponent_rating: float) -> float:
    return 1 / (1 + 10 ** ((opponent_rating - player_rating) / 400))


def update_elo(player_rating: float, opponent_rating: float, actual_score: float, k_factor: float = 24.0) -> float:
    expected = expected_score(player_rating, opponent_rating)
    return player_rating + k_factor * (actual_score - expected)
