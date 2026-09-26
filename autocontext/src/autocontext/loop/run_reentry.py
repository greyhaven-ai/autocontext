"""Re-entering an existing run: refuse runs the loop must not continue, and repair state a dead process left."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, cast

from autocontext.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)
# Run rows the generation loop did not write: agent-task `run`, task-like `solve`, package import.
NON_LOOP_EXECUTOR_MODES = frozenset({"agent_task", "artifact_editing", "import"})


def _int_value(value: object, default: int = 0) -> int:
    try:
        return int(cast(int | float | str, value))
    except (TypeError, ValueError):
        return default


def validate_reentry(run_row: dict[str, Any], *, run_id: str, scenario_name: str) -> None:
    """Refuse to re-enter a run this loop cannot continue, before anything is written."""
    # A stopped run is terminal (first-terminal-outcome-wins): refuse to
    # resume it into 'running', which would let it later be marked
    # 'completed' and overwrite the terminal outcome. Restart under a new id.
    if str(run_row.get("status") or "") == "stopped":
        raise ValueError(f"run '{run_id}' was stopped and is terminal; start a new run id to continue")
    stored_scenario = run_row.get("scenario")
    if isinstance(stored_scenario, str) and stored_scenario != scenario_name:
        raise ValueError(f"run '{run_id}' belongs to scenario '{stored_scenario}', not '{scenario_name}'")
    if run_row.get("executor_mode") in NON_LOOP_EXECUTOR_MODES:
        raise ValueError(f"run '{run_id}' was not created by the generation loop and cannot be resumed")


def recover_stale_run_state(sqlite: SQLiteStore, run_id: str) -> None:
    """Repair an interrupted run before attempting a resume.

    This handles the persisted broken state left behind after a prior process
    died or was interrupted while a run still had `running` rows in SQLite.
    """
    run_row = sqlite.get_run(run_id)
    if run_row is None or str(run_row.get("status") or "") != "running":
        return

    generation_rows = sqlite.get_generation_metrics(run_id)
    running_generations = [
        _int_value(row.get("generation_index")) for row in generation_rows if str(row.get("status") or "") == "running"
    ]
    if running_generations:
        recovery_markers = sqlite.get_recovery_markers_for_run(run_id)
        retry_counts: dict[int, int] = defaultdict(int)
        for marker in recovery_markers:
            retry_counts[_int_value(marker.get("generation_index"))] += 1
        for generation_index in running_generations:
            sqlite.update_generation_status(
                run_id,
                generation_index,
                status="failed",
                gate_decision="stalled",
            )
            sqlite.append_recovery_marker(
                run_id,
                generation_index,
                decision="mark_failed",
                reason="Recovered stale running generation from a prior interrupted run",
                retry_count=retry_counts[generation_index] + 1,
            )
        sqlite.mark_run_failed(run_id)
        logger.warning(
            "recovered stale running generations for run %s: %s",
            run_id,
            ", ".join(str(gen) for gen in running_generations),
        )
        return

    completed_generations = sum(1 for row in generation_rows if str(row.get("status") or "") == "completed")
    target_generations = _int_value(run_row.get("target_generations"), 0)
    if target_generations > 0 and completed_generations >= target_generations:
        sqlite.mark_run_completed(run_id)
        logger.info(
            "marking run %s completed during recovery (%d/%d generations already completed)",
            run_id,
            completed_generations,
            target_generations,
        )
        return

    sqlite.mark_run_failed(run_id)
    logger.warning(
        "marking run %s failed during recovery; run was still 'running' without an active generation",
        run_id,
    )
