"""Stage helpers — dimensions (extracted from stages.py, AC-482)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from autocontext.harness.evaluation.types import EvaluationSummary

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.storage import SQLiteStore


def _load_previous_best_dimensions(
    sqlite: SQLiteStore,
    run_id: str,
) -> dict[str, float]:
    """Read the latest persisted generation dimensions for regression comparison."""
    try:
        rows = sqlite.get_generation_trajectory(run_id)
    except Exception:
        logger.debug("failed to load previous dimension summary", exc_info=True)
        return {}
    if not isinstance(rows, list) or not rows:
        return {}
    latest = rows[-1]
    if not isinstance(latest, dict):
        return {}
    summary = latest.get("dimension_summary")
    if not isinstance(summary, dict):
        return {}
    raw_best = summary.get("best_dimensions")
    if not isinstance(raw_best, dict):
        return {}
    return {
        name: float(value)
        for name, value in raw_best.items()
        if isinstance(name, str) and isinstance(value, (int, float))
    }


def _coerce_dimension_score_map(raw_value: Any) -> dict[str, float]:
    """Return a JSON-safe dimension score mapping."""
    if not isinstance(raw_value, dict):
        return {}
    return {
        name: round(float(value), 6)
        for name, value in raw_value.items()
        if isinstance(name, str) and isinstance(value, (int, float))
    }


def _coerce_dimension_specs(raw_value: Any) -> list[dict[str, Any]]:
    """Return JSON-safe dimension specs."""
    if not isinstance(raw_value, list):
        return []
    specs: list[dict[str, Any]] = []
    for item in raw_value:
        if not isinstance(item, dict):
            continue
        clean: dict[str, Any] = {
            key: value
            for key, value in item.items()
            if isinstance(key, str)
            and (value is None or isinstance(value, (str, int, float, bool)))
        }
        if clean:
            specs.append(clean)
    return specs


def _coerce_dimension_regressions(raw_value: Any) -> list[dict[str, Any]]:
    """Return JSON-safe dimension regression payloads."""
    if not isinstance(raw_value, list):
        return []
    regressions: list[dict[str, Any]] = []
    for item in raw_value:
        if not isinstance(item, dict):
            continue
        dimension = item.get("dimension")
        previous = item.get("previous")
        current = item.get("current")
        delta = item.get("delta")
        if not isinstance(dimension, str):
            continue
        if not isinstance(previous, (int, float)):
            continue
        if not isinstance(current, (int, float)):
            continue
        if not isinstance(delta, (int, float)):
            continue
        regressions.append({
            "dimension": dimension,
            "previous": round(float(previous), 6),
            "current": round(float(current), 6),
            "delta": round(float(delta), 6),
        })
    return regressions


def _build_dimension_summary_payload(tournament: EvaluationSummary) -> dict[str, Any] | None:
    """Extract a JSON-safe dimensional summary from a tournament."""
    dimension_means = _coerce_dimension_score_map(getattr(tournament, "dimension_means", {}))
    best_dimensions = _coerce_dimension_score_map(getattr(tournament, "best_dimensions", {}))
    dimension_specs = _coerce_dimension_specs(getattr(tournament, "dimension_specs", []))
    dimension_regressions = _coerce_dimension_regressions(
        getattr(tournament, "dimension_regressions", []),
    )
    if not any((dimension_means, best_dimensions, dimension_specs, dimension_regressions)):
        return None
    return {
        "dimension_means": dimension_means,
        "best_dimensions": best_dimensions,
        "dimension_specs": dimension_specs,
        "dimension_regressions": dimension_regressions,
    }


def _build_self_play_summary_payload(tournament: EvaluationSummary) -> dict[str, Any] | None:
    """Extract a JSON-safe self-play summary from a tournament."""
    raw_value = getattr(tournament, "self_play_summary", {})
    if not isinstance(raw_value, dict):
        return None
    clean: dict[str, Any] = {}
    for key, value in raw_value.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, (bool, str)):
            clean[key] = value
            continue
        if isinstance(value, int):
            clean[key] = value
            continue
        if isinstance(value, float):
            clean[key] = round(value, 6)
    return clean or None


def _json_dumps_if_serializable(value: Any) -> str | None:
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return None


def _build_replay_envelope_payload(execution_output: Any) -> dict[str, Any]:
    replay = getattr(execution_output, "replay", None)
    model_dump = getattr(replay, "model_dump", None)
    if not callable(model_dump):
        return {}
    try:
        payload = model_dump()
    except Exception:
        logger.debug("loop.stages: caught Exception", exc_info=True)
        return {}
    if not isinstance(payload, dict):
        return {}
    if _json_dumps_if_serializable(payload) is None:
        return {}
    return payload


def _build_match_replay_json(execution_output: Any) -> str:
    result = getattr(execution_output, "result", None)
    replay = getattr(result, "replay", None)
    if replay:
        serialized = _json_dumps_if_serializable(replay)
        if serialized is not None:
            return serialized

    replay_payload = _build_replay_envelope_payload(execution_output)
    timeline = replay_payload.get("timeline")
    if timeline:
        serialized = _json_dumps_if_serializable(timeline)
        if serialized is not None:
            return serialized
    return ""
