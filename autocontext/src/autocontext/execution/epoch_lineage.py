"""Registry-aware epoch-lineage annotation for operator read surfaces (AC-885 Slice D1).

Separate from the leaf ``evaluator_epoch`` module because it depends on the registry (which imports
the classifier), so co-locating would cycle. Adds no score, judge call, or promotion: it reads the
scenario's active epoch (via ``EvaluatorEpochRegistry.active_for``, whose own contract may lock and
self-heal a multiple-active state) and classifies each status row.
"""

from __future__ import annotations

from typing import Any

from autocontext.execution.evaluator_epoch import classify_epoch_lineage
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry


def annotate_status_rows(
    rows: list[dict[str, Any]],
    scenario: str | None,
    registry: EvaluatorEpochRegistry,
) -> tuple[list[dict[str, Any]], str | None]:
    """Return (row copies each with an ``evaluator_epoch_status`` key, active_epoch_id).

    The scenario's active epoch is read once. ``scenario is None`` yields ``no_active_epoch`` for every
    row. Inputs are not mutated.
    """
    active = registry.active_for(scenario) if scenario else None
    active_id = active.epoch_id if active is not None else None
    annotated: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        copy["evaluator_epoch_status"] = classify_epoch_lineage(copy.get("evaluator_epoch"), active_id)
        annotated.append(copy)
    return annotated, active_id
