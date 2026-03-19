"""Tests for AC-319: pluggable scoring backends.

Covers: ScoringBackend ABC, TrialResult, RatingUpdate,
EloBackend, GlickoBackend, get_backend.
"""

from __future__ import annotations

# ===========================================================================
# TrialResult — continuous score preservation
# ===========================================================================


class TestTrialResult:
    def test_construction(self) -> None:
        from autocontext.harness.scoring.backends import TrialResult

        tr = TrialResult(score=0.75, seed=42, opponent_rating=1000.0)
        assert tr.score == 0.75
        assert tr.seed == 42

    def test_win_loss_from_threshold(self) -> None:
        from autocontext.harness.scoring.backends import TrialResult

        win = TrialResult(score=0.7, seed=1, opponent_rating=1000.0)
        assert win.is_win(threshold=0.55) is True

        loss = TrialResult(score=0.3, seed=2, opponent_rating=1000.0)
        assert loss.is_win(threshold=0.55) is False

    def test_roundtrip(self) -> None:
        from autocontext.harness.scoring.backends import TrialResult

        tr = TrialResult(score=0.6, seed=10, opponent_rating=1050.0)
        d = tr.to_dict()
        restored = TrialResult.from_dict(d)
        assert restored.score == 0.6


# ===========================================================================
# RatingUpdate
# ===========================================================================


class TestRatingUpdate:
    def test_construction(self) -> None:
        from autocontext.harness.scoring.backends import RatingUpdate

        update = RatingUpdate(
            rating_before=1000.0,
            rating_after=1025.0,
            uncertainty_before=350.0,
            uncertainty_after=320.0,
            backend_name="glicko",
        )
        assert update.rating_after == 1025.0
        assert update.backend_name == "glicko"


# ===========================================================================
# EloBackend
# ===========================================================================


class TestEloBackend:
    def test_name(self) -> None:
        from autocontext.harness.scoring.backends import EloBackend

        assert EloBackend().name == "elo"

    def test_update_on_wins(self) -> None:
        from autocontext.harness.scoring.backends import EloBackend, TrialResult

        backend = EloBackend()
        trials = [
            TrialResult(score=0.8, seed=1, opponent_rating=1000.0),
            TrialResult(score=0.7, seed=2, opponent_rating=1000.0),
            TrialResult(score=0.9, seed=3, opponent_rating=1000.0),
        ]
        update = backend.update(current_rating=1000.0, trials=trials)
        assert update.rating_after > 1000.0

    def test_update_on_losses(self) -> None:
        from autocontext.harness.scoring.backends import EloBackend, TrialResult

        backend = EloBackend()
        trials = [
            TrialResult(score=0.3, seed=1, opponent_rating=1000.0),
            TrialResult(score=0.2, seed=2, opponent_rating=1000.0),
        ]
        update = backend.update(current_rating=1000.0, trials=trials)
        assert update.rating_after < 1000.0

    def test_preserves_continuous_scores(self) -> None:
        from autocontext.harness.scoring.backends import EloBackend, TrialResult

        backend = EloBackend()
        trials = [TrialResult(score=0.6, seed=1, opponent_rating=1000.0)]
        update = backend.update(current_rating=1000.0, trials=trials)
        # Continuous scores are in metadata
        assert len(update.metadata.get("trial_scores", [])) == 1

    def test_continuous_scores_change_rating_delta(self) -> None:
        from autocontext.harness.scoring.backends import EloBackend, TrialResult

        backend = EloBackend()
        modest = backend.update(
            current_rating=1000.0,
            trials=[TrialResult(score=0.56, seed=1, opponent_rating=1000.0)],
        )
        strong = backend.update(
            current_rating=1000.0,
            trials=[TrialResult(score=0.96, seed=1, opponent_rating=1000.0)],
        )
        assert strong.rating_after > modest.rating_after

    def test_no_uncertainty(self) -> None:
        from autocontext.harness.scoring.backends import EloBackend, TrialResult

        backend = EloBackend()
        trials = [TrialResult(score=0.7, seed=1, opponent_rating=1000.0)]
        update = backend.update(current_rating=1000.0, trials=trials)
        assert update.uncertainty_after is None  # Elo has no uncertainty


# ===========================================================================
# GlickoBackend — uncertainty-aware
# ===========================================================================


class TestGlickoBackend:
    def test_name(self) -> None:
        from autocontext.harness.scoring.backends import GlickoBackend

        assert GlickoBackend().name == "glicko"

    def test_update_has_uncertainty(self) -> None:
        from autocontext.harness.scoring.backends import GlickoBackend, TrialResult

        backend = GlickoBackend()
        trials = [
            TrialResult(score=0.8, seed=1, opponent_rating=1000.0),
            TrialResult(score=0.7, seed=2, opponent_rating=1000.0),
        ]
        update = backend.update(
            current_rating=1500.0, trials=trials, uncertainty=350.0,
        )
        assert update.uncertainty_after is not None
        assert update.uncertainty_after < 350.0  # Uncertainty decreases with data

    def test_uncertainty_decreases_with_more_trials(self) -> None:
        from autocontext.harness.scoring.backends import GlickoBackend, TrialResult

        backend = GlickoBackend()
        few_trials = [TrialResult(score=0.7, seed=1, opponent_rating=1000.0)]
        many_trials = [TrialResult(score=0.7, seed=i, opponent_rating=1000.0) for i in range(10)]

        update_few = backend.update(current_rating=1500.0, trials=few_trials, uncertainty=350.0)
        update_many = backend.update(current_rating=1500.0, trials=many_trials, uncertainty=350.0)

        assert update_many.uncertainty_after is not None
        assert update_few.uncertainty_after is not None
        assert update_many.uncertainty_after < update_few.uncertainty_after

    def test_continuous_scores_change_rating_delta(self) -> None:
        from autocontext.harness.scoring.backends import GlickoBackend, TrialResult

        backend = GlickoBackend()
        modest = backend.update(
            current_rating=1500.0,
            trials=[TrialResult(score=0.56, seed=1, opponent_rating=1500.0)],
            uncertainty=350.0,
        )
        strong = backend.update(
            current_rating=1500.0,
            trials=[TrialResult(score=0.96, seed=1, opponent_rating=1500.0)],
            uncertainty=350.0,
        )
        assert strong.rating_after > modest.rating_after


# ===========================================================================
# get_backend
# ===========================================================================


class TestGetBackend:
    def test_get_elo(self) -> None:
        from autocontext.harness.scoring.backends import get_backend

        backend = get_backend("elo")
        assert backend.name == "elo"

    def test_get_glicko(self) -> None:
        from autocontext.harness.scoring.backends import get_backend

        backend = get_backend("glicko")
        assert backend.name == "glicko"

    def test_get_unknown_falls_back_to_elo(self) -> None:
        from autocontext.harness.scoring.backends import get_backend

        backend = get_backend("unknown")
        assert backend.name == "elo"
