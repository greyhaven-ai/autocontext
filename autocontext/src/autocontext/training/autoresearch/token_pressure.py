"""Token-pressure diagnostics for OPD/GKD teacher signals."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any

DEFAULT_DIAGNOSTIC_MAX_PROMPTS = 8
DEFAULT_DIAGNOSTIC_MAX_TOKENS = 64


@dataclass(frozen=True, slots=True)
class TokenPressureObservation:
    position: int
    student_logprob: float
    teacher_logprob: float
    student_entropy: float | None = None
    token_text: str | None = None

    @property
    def margin(self) -> float:
        return self.teacher_logprob - self.student_logprob


def build_token_pressure_report(
    observations: list[TokenPressureObservation],
    *,
    backend: str,
    mode: str,
    seed: int = 0,
    run_id: str = "",
    response_lengths: list[int] | None = None,
    shock_threshold: float = 2.0,
    include_token_text: bool = False,
) -> dict[str, Any]:
    margins = [obs.margin for obs in observations]
    positive = [m for m in margins if m > 0]
    negative = [m for m in margins if m < 0]
    shocks = [obs for obs in observations if abs(obs.margin) >= shock_threshold]
    total = len(observations)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "backend": backend,
        "mode": mode,
        "seed": seed,
        "token_count": total,
        "positive_pressure_ratio": _ratio(len(positive), total),
        "negative_pressure_ratio": _ratio(len(negative), total),
        "neutral_pressure_ratio": _ratio(total - len(positive) - len(negative), total),
        "mean_positive_margin": fmean(positive) if positive else None,
        "mean_negative_margin": fmean(negative) if negative else None,
        "mean_margin": fmean(margins) if margins else None,
        "mean_student_entropy": _mean([obs.student_entropy for obs in observations]),
        "mean_response_length": fmean(response_lengths) if response_lengths else None,
        "position_pressure": _position_pressure(observations),
        "shock_threshold": shock_threshold,
        "shock_spike_count": len(shocks),
        "shock_spikes": [_shock(obs, include_token_text=include_token_text) for obs in shocks],
        "raw_token_text_persisted": include_token_text,
    }


def write_token_pressure_report(path: Path, report: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def bounded_diagnostic_inputs(
    items: Sequence[Any],
    max_tokens: int,
    *,
    remaining_seconds: float,
    max_prompts: int = DEFAULT_DIAGNOSTIC_MAX_PROMPTS,
    max_diagnostic_tokens: int = DEFAULT_DIAGNOSTIC_MAX_TOKENS,
) -> tuple[list[Any], int]:
    if remaining_seconds <= 0.0 or max_tokens <= 0:
        return [], 0
    return list(items)[:max_prompts], min(max_tokens, max_diagnostic_tokens)


def compare_token_pressure_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_count": len(reports),
        "mean_positive_pressure_ratio": _mean(_numbers(reports, "positive_pressure_ratio")),
        "mean_negative_pressure_ratio": _mean(_numbers(reports, "negative_pressure_ratio")),
        "highest_positive_pressure_run_id": _max_run(reports, "positive_pressure_ratio"),
        "highest_shock_run_id": _max_run(reports, "shock_spike_count"),
        "runs": [
            {
                "run_id": str(report.get("run_id", "")),
                "positive_pressure_ratio": report.get("positive_pressure_ratio"),
                "negative_pressure_ratio": report.get("negative_pressure_ratio"),
                "shock_spike_count": report.get("shock_spike_count", 0),
            }
            for report in reports
        ],
    }


def _position_pressure(observations: list[TokenPressureObservation]) -> list[dict[str, Any]]:
    by_position: dict[int, list[TokenPressureObservation]] = defaultdict(list)
    for obs in observations:
        by_position[obs.position].append(obs)
    rows: list[dict[str, Any]] = []
    for position, items in sorted(by_position.items()):
        margins = [obs.margin for obs in items]
        rows.append(
            {
                "position": position,
                "count": len(items),
                "positive_pressure_ratio": _ratio(sum(1 for margin in margins if margin > 0), len(items)),
                "negative_pressure_ratio": _ratio(sum(1 for margin in margins if margin < 0), len(items)),
                "mean_margin": fmean(margins),
                "mean_student_entropy": _mean([obs.student_entropy for obs in items]),
            }
        )
    return rows


def _shock(obs: TokenPressureObservation, *, include_token_text: bool) -> dict[str, Any]:
    item: dict[str, Any] = {
        "position": obs.position,
        "margin": obs.margin,
        "direction": "positive" if obs.margin > 0 else "negative",
    }
    if include_token_text:
        item["token_text"] = obs.token_text or ""
    return item


def _ratio(count: int, total: int) -> float:
    return 0.0 if total == 0 else count / total


def _mean(values: Sequence[float | int | None]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    return fmean(numeric) if numeric else None


def _numbers(reports: list[dict[str, Any]], key: str) -> list[float]:
    return [float(report[key]) for report in reports if isinstance(report.get(key), int | float)]


def _max_run(reports: list[dict[str, Any]], key: str) -> str | None:
    if not reports:
        return None
    winner = max(reports, key=lambda report: float(report.get(key) or 0.0))
    return str(winner.get("run_id", ""))


__all__ = [
    "DEFAULT_DIAGNOSTIC_MAX_PROMPTS",
    "DEFAULT_DIAGNOSTIC_MAX_TOKENS",
    "TokenPressureObservation",
    "bounded_diagnostic_inputs",
    "build_token_pressure_report",
    "compare_token_pressure_reports",
    "write_token_pressure_report",
]
