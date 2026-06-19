"""Soft structural hint policy and A/B reporting (AC-796)."""

from __future__ import annotations

from collections import defaultdict
from statistics import fmean
from typing import Any, Literal

HintStyle = Literal["default", "structural", "solution_like"]

STRUCTURAL_HINT_POLICY = (
    "Structural hint policy: prefer constraints, invariants, verification checks, "
    "promising representations, and repair directions; avoid full target solutions, "
    "exact parameter recipes, and route-locking commitments unless the user explicitly asks."
)

_ROUTE_TERMS = ("exact", "set ", "must use", "full solution", "parameter recipe", "route")
_STRUCTURAL_TERMS = ("constraint", "invariant", "check", "verify", "repair", "representation")


def effective_hint_style(*, soft_hints_enabled: bool, hint_style: str) -> HintStyle:
    normalized = hint_style.strip().lower()
    if soft_hints_enabled:
        return "structural"
    if normalized == "structural":
        return "structural"
    if normalized == "solution_like":
        return "solution_like"
    return "default"


def structural_hint_prompt(hint_style: str) -> str:
    return STRUCTURAL_HINT_POLICY if hint_style == "structural" else ""


def build_hint_metadata(text: str, *, hint_style: str, support_evidence: str = "") -> dict[str, Any]:
    lowered = text.lower()
    route_prescriptive = any(term in lowered for term in _ROUTE_TERMS)
    structurally_worded = any(term in lowered for term in _STRUCTURAL_TERMS)
    return {
        "hint_style": hint_style,
        "support_evidence": support_evidence,
        "is_structural": hint_style == "structural" or structurally_worded,
        "route_prescriptive": route_prescriptive,
    }


def build_hint_ab_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("hint_style") or "default")].append(row)
    return {
        "schema_version": 1,
        "styles": {style: _summarize_style(items) for style, items in sorted(grouped.items())},
    }


def _summarize_style(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "run_count": len(rows),
        "mean_score": _mean(rows, "score"),
        "mean_response_length": _mean(rows, "response_length"),
        "mean_novelty": _mean(rows, "novelty"),
        "rollback_rate": _rate(rows, "rolled_back"),
        "hint_adoption_rate": _rate(rows, "hint_adopted"),
    }


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if isinstance(row.get(key), int | float)]
    return fmean(values) if values else None


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if bool(row.get(key))) / len(rows)


__all__ = [
    "HintStyle",
    "STRUCTURAL_HINT_POLICY",
    "build_hint_ab_report",
    "build_hint_metadata",
    "effective_hint_style",
    "structural_hint_prompt",
]
