"""Small helpers for experimental OPD pressure modes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

OPD_PRESSURE_MODES: Final = ("full_kl", "sample_positive", "sample_positive_reverse_negative")
OPD_PRESSURE_MODE_CODES: Final[dict[str, float]] = {
    "full_kl": 0.0,
    "sample_positive": 1.0,
    "sample_positive_reverse_negative": 2.0,
}


def normalize_opd_pressure_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized not in OPD_PRESSURE_MODE_CODES:
        expected = "|".join(OPD_PRESSURE_MODES)
        raise ValueError(f"opd_pressure_mode must be one of {expected}, got {mode!r}")
    return normalized


def selected_sample_mask(margins: Sequence[float], mode: str) -> list[bool]:
    normalized = normalize_opd_pressure_mode(mode)
    if normalized == "sample_positive":
        return [margin > 0.0 for margin in margins]
    if normalized == "sample_positive_reverse_negative":
        return [margin != 0.0 for margin in margins]
    return [True for _ in margins]


def sampled_token_pressure_summary(margins: Sequence[float], mode: str) -> dict[str, float]:
    normalized = normalize_opd_pressure_mode(mode)
    total = len(margins)
    if total == 0:
        return {
            "opd_positive_token_fraction": 0.0,
            "opd_negative_token_fraction": 0.0,
            "opd_mean_masked_loss": 0.0,
        }

    mask = selected_sample_mask(margins, normalized)
    losses = [abs(margin) for margin, selected in zip(margins, mask, strict=True) if selected]
    return {
        "opd_positive_token_fraction": sum(1 for margin in margins if margin > 0.0) / total,
        "opd_negative_token_fraction": sum(1 for margin in margins if margin < 0.0) / total,
        "opd_mean_masked_loss": sum(losses) / len(losses) if losses else 0.0,
    }
