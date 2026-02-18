"""Tests for mts.harness.scoring.elo — Elo rating utilities."""

from __future__ import annotations

import pytest

from mts.harness.scoring.elo import expected_score, update_elo


class TestExpectedScore:
    def test_expected_score_equal_ratings(self) -> None:
        assert expected_score(1000, 1000) == pytest.approx(0.5)

    def test_expected_score_higher_player(self) -> None:
        score = expected_score(1200, 1000)
        assert score > 0.5

    def test_expected_score_lower_player(self) -> None:
        score = expected_score(800, 1000)
        assert score < 0.5


class TestUpdateElo:
    def test_update_elo_win(self) -> None:
        new_rating = update_elo(1000, 1000, 1.0)
        assert new_rating > 1000

    def test_update_elo_loss(self) -> None:
        new_rating = update_elo(1000, 1000, 0.0)
        assert new_rating < 1000

    def test_update_elo_custom_k_factor(self) -> None:
        default_k = update_elo(1000, 1000, 1.0)
        high_k = update_elo(1000, 1000, 1.0, k_factor=48.0)
        assert high_k > default_k

    def test_update_elo_expected_defeats_no_change(self) -> None:
        # Much stronger player wins as expected → minimal Elo change
        new_rating = update_elo(1600, 1000, 1.0)
        delta = abs(new_rating - 1600)
        # Expected score ~0.97 so actual-expected ~0.03, change ~0.03*24 ≈ 0.7
        assert delta < 2.0
