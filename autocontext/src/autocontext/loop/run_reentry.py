"""Re-entering an existing run: refuse runs the loop must not continue, and repair state a dead process left.

An allowed switch of the run's recorded runtime is announced rather than written to the row.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, cast

from autocontext.config.settings import AppSettings
from autocontext.context_bundles.assembly import bundle_routing_config
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.loop.events import EventStreamEmitter
from autocontext.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)
# Run rows the generation loop did not write: agent-task `run`, task-like `solve`, package import.
NON_LOOP_EXECUTOR_MODES = frozenset({"agent_task", "artifact_editing", "import"})
# The runtime a run row records, and the variable that selects each value.
RUNTIME_VARIABLES = {"agent_provider": "AUTOCONTEXT_AGENT_PROVIDER", "executor_mode": "AUTOCONTEXT_EXECUTOR_MODE"}


def _int_value(value: object, default: int = 0) -> int:
    try:
        return int(cast(int | float | str, value))
    except (TypeError, ValueError):
        return default


def runtime_changes(run_row: dict[str, Any], settings: AppSettings) -> dict[str, tuple[str, str]]:
    """Stored runtime values the current settings differ from, as field -> (stored, current).

    An empty or missing stored value (a row from before migration 005) is unknown, so it is never a change.
    """
    changes: dict[str, tuple[str, str]] = {}
    for field in RUNTIME_VARIABLES:
        stored, current = run_row.get(field), str(getattr(settings, field))
        if isinstance(stored, str) and stored and stored != current:
            changes[field] = (stored, current)
    return changes


def runtime_change_error(run_id: str, changes: dict[str, tuple[str, str]]) -> str:
    created = " and ".join(f"{field} '{stored}'" for field, (stored, _) in changes.items())
    current = " and ".join(f"{field} '{value}'" for field, (_, value) in changes.items())
    restore = " ".join(f"{RUNTIME_VARIABLES[field]}={stored}" for field, (stored, _) in changes.items())
    return (
        f"run '{run_id}' was created with {created}, but the current settings use {current}; set {restore} "
        f"to continue it, or run `autoctx resume {run_id} --allow-runtime-change` to switch deliberately"
    )


def _pinned_provider(scenario_name: str, settings: AppSettings) -> object:
    """The agent_provider the scenario's active context bundle pins, or None when no served bundle pins one.

    Ablation never serves the bundle, and a damaged one is reported by the generation that serves it.
    """
    if settings.ablation_no_feedback:
        return None
    try:
        bundle = ContextBundleStore(settings.knowledge_root).active_bundle(scenario_name)
        return bundle_routing_config(bundle).get("agent_provider") if bundle is not None else None
    except (KeyError, OSError, ValueError):
        return None


def runtime_refusal(
    run_row: dict[str, Any],
    *,
    run_id: str,
    scenario_name: str,
    settings: AppSettings,
    allow_runtime_change: bool,
) -> str | None:
    """Why the current runtime may not continue the run, or None; the runner and ``autoctx resume`` share it."""
    # Only the provider name and executor mode are stored, so continuing under the stored values would pair
    # them with this process's credentials and endpoints; refuse unless the switch is deliberate.
    changes = runtime_changes(run_row, settings)
    if changes and not allow_runtime_change:
        return runtime_change_error(run_id, changes)
    # Each generation serves the scenario's active context bundle and fails on a runtime built for another
    # provider than the one it pins, so a deliberate provider switch cannot complete while that pin stands.
    pinned = _pinned_provider(scenario_name, settings) if "agent_provider" in changes else None
    if pinned is not None and pinned != settings.agent_provider:
        return (
            f"run '{run_id}' cannot switch to agent_provider '{settings.agent_provider}': the active context bundle "
            f"of scenario '{scenario_name}' pins agent_provider '{pinned}'; set "
            f"{RUNTIME_VARIABLES['agent_provider']}={pinned} to continue it"
        )
    return None


def validate_reentry(
    run_row: dict[str, Any],
    *,
    run_id: str,
    scenario_name: str,
    settings: AppSettings,
    allow_runtime_change: bool = False,
) -> bool:
    """Refuse to re-enter a run this loop cannot continue, before anything is written.

    Returns whether the re-entry switches the run's recorded runtime, which only ``allow_runtime_change`` permits.
    """
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
    refusal = runtime_refusal(
        run_row, run_id=run_id, scenario_name=scenario_name, settings=settings, allow_runtime_change=allow_runtime_change
    )
    if refusal:
        raise ValueError(refusal)
    return bool(runtime_changes(run_row, settings))


def announce_runtime_change(
    events: EventStreamEmitter,
    sqlite: SQLiteStore,
    run_row: dict[str, Any],
    settings: AppSettings,
    target_generations: int,
) -> None:
    """Record an allowed runtime switch; the run row keeps the runtime it was created with."""
    run_id = str(run_row["run_id"])
    done = {
        _int_value(row.get("generation_index"))
        for row in sqlite.get_generation_metrics(run_id)
        if str(row.get("status") or "") == "completed"
    }
    # None when every generation is done and only the post-run tail runs under the new runtime.
    first_generation = next((gen for gen in range(1, target_generations + 1) if gen not in done), None)
    created = {field: str(run_row.get(field) or "") for field in RUNTIME_VARIABLES}
    current = {field: str(getattr(settings, field)) for field in RUNTIME_VARIABLES}
    logger.warning(
        "run %s continues under %s instead of %s from generation %s; its row keeps the original runtime",
        run_id,
        current,
        created,
        first_generation,
    )
    events.emit(
        "run_runtime_changed",
        {"run_id": run_id, "from": created, "to": current, "first_generation": first_generation},
    )


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

    # Failed even when every generation is done: its post-run tail (report, knowledge snapshot, run_completed)
    # may not have run, and a run completed at its target is left unchanged on re-entry, so the next resume
    # must still see it as unfinished.
    sqlite.mark_run_failed(run_id)
    logger.warning(
        "marking run %s failed during recovery; run was still 'running' without an active generation",
        run_id,
    )
