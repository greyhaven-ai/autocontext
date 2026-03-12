from autocontext.execution.elo import expected_score, update_elo


def test_elo_update_rewards_win() -> None:
    baseline = 1000.0
    updated = update_elo(baseline, 1000.0, actual_score=1.0)
    assert updated > baseline


def test_expected_score_balanced_at_equal_ratings() -> None:
    assert expected_score(1000.0, 1000.0) == 0.5
