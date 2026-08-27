"""Canonical wire payload helpers shared by run-inspection commands."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def run_wire_payload(run: Mapping[str, Any], *, run_id: str | None = None) -> dict[str, Any]:
    """Return the stable cross-runtime Run projection used by CLI JSON."""
    payload: dict[str, Any] = {
        "run_id": str(run.get("run_id") or run_id or ""),
        "scenario": str(run.get("scenario") or ""),
        "target_generations": int(run.get("target_generations") or 0),
        "executor_mode": str(run.get("executor_mode") or ""),
        "status": str(run.get("status") or ""),
        "agent_provider": str(run.get("agent_provider") or ""),
        "created_at": _nullable_string(run.get("created_at")),
        "updated_at": _nullable_string(run.get("updated_at")),
    }
    minimum_generations = int(run.get("minimum_generations") or 1)
    if minimum_generations > 1:
        payload["minimum_generations"] = minimum_generations
    return payload


def generation_wire_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable cross-runtime Generation projection used by CLI JSON."""
    return {
        "generation_index": int(row.get("generation_index") or 0),
        "mean_score": float(row.get("mean_score") or 0.0),
        "best_score": float(row.get("best_score") or 0.0),
        "elo": float(row.get("elo") or 0.0),
        "wins": int(row.get("wins") or 0),
        "losses": int(row.get("losses") or 0),
        "gate_decision": str(row.get("gate_decision") or ""),
        "status": str(row.get("status") or ""),
        "duration_seconds": _nullable_float(row.get("duration_seconds")),
        "evaluator_epoch": _nullable_string(row.get("evaluator_epoch")),
        "quarantined": _nullable_int(row.get("quarantined")),
        "created_at": _nullable_string(row.get("created_at")),
        "updated_at": _nullable_string(row.get("updated_at")),
    }


def run_status_wire_payload(
    run: Mapping[str, Any],
    generations: Sequence[Mapping[str, Any]],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build the single-JSON/NDJSON status envelope shared by both runtimes."""
    latest = max(generations, key=lambda row: int(row.get("generation_index") or 0), default=None)
    return {
        "run": run_wire_payload(run, run_id=run_id),
        "latest_generation": generation_wire_payload(latest) if latest is not None else None,
        "runtime_session": None,
        "progress_report": None,
    }


def run_show_wire_payload(
    run: Mapping[str, Any],
    generation: Mapping[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build the selected-generation envelope shared by both runtimes."""
    return {
        "run": run_wire_payload(run, run_id=run_id),
        "generation": generation_wire_payload(generation),
        "runtime_session": None,
    }


def _nullable_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _nullable_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _nullable_int(value: Any) -> int | None:
    return None if value is None else int(value)


__all__ = [
    "generation_wire_payload",
    "run_show_wire_payload",
    "run_status_wire_payload",
    "run_wire_payload",
]
