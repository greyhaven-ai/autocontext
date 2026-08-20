from __future__ import annotations

from autocontext.analytics.false_promotion_calibration import (
    FalsePromotionCalibrationCase,
    simulate_false_promotion_campaigns,
)
from autocontext.context_bundles import CampaignFalsePromotionPolicy


def test_full_campaign_null_rate_and_power_are_calibrated_deterministically() -> None:
    cluster_t = CampaignFalsePromotionPolicy()
    robust = CampaignFalsePromotionPolicy(robust_method="bounded_hoeffding")
    cases = {
        "null_normal": simulate_false_promotion_campaigns(
            cluster_t,
            FalsePromotionCalibrationCase("null_normal", 0.0, "normal", 12),
            campaigns=500,
        ),
        "clear_win": simulate_false_promotion_campaigns(
            cluster_t,
            FalsePromotionCalibrationCase("clear_win", 0.4, "normal", 12),
            campaigns=500,
        ),
        "near_tie": simulate_false_promotion_campaigns(
            cluster_t,
            FalsePromotionCalibrationCase("near_tie", 0.03, "normal", 12),
            campaigns=500,
        ),
        "heteroskedastic": simulate_false_promotion_campaigns(
            cluster_t,
            FalsePromotionCalibrationCase("heteroskedastic", 0.3, "heteroskedastic", 24),
            campaigns=500,
        ),
        "null_heavy_tail": simulate_false_promotion_campaigns(
            robust,
            FalsePromotionCalibrationCase("null_heavy_tail", 0.0, "bounded_heavy_tail", 128),
            campaigns=500,
        ),
        "heavy_tail_win": simulate_false_promotion_campaigns(
            robust,
            FalsePromotionCalibrationCase("heavy_tail_win", 0.35, "bounded_heavy_tail", 128),
            campaigns=500,
        ),
    }

    assert cases["null_normal"].promotion_rate <= cluster_t.familywise_alpha
    assert cases["null_heavy_tail"].promotion_rate <= robust.familywise_alpha
    assert cases["clear_win"].promotion_rate >= 0.9
    assert cases["heteroskedastic"].promotion_rate >= 0.8
    assert cases["heavy_tail_win"].promotion_rate >= 0.8
    assert cases["near_tie"].promotion_rate < 0.2
    assert cases["clear_win"].average_candidates_evaluated < cases["near_tie"].average_candidates_evaluated
    assert cases["heavy_tail_win"].average_confirmation_blocks >= 128
