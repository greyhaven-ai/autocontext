"""Run-id guard for the agent-task path of ``autoctx run``."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autocontext.storage import SQLiteStore


def refuse_existing_agent_task_run(sqlite: SQLiteStore, run_id: str | None, scenario_name: str) -> None:
    """Agent-task runs are one-shot: refuse a requested run id that already exists, before any write."""
    existing = sqlite.get_run(run_id) if run_id else None
    if existing is None:
        return
    if str(existing.get("status") or "") == "stopped":
        raise ValueError(f"run '{run_id}' was stopped and is terminal; start a new run id to continue")
    stored_scenario = existing.get("scenario")
    if isinstance(stored_scenario, str) and stored_scenario != scenario_name:
        raise ValueError(f"run '{run_id}' belongs to scenario '{stored_scenario}', not '{scenario_name}'")
    if existing.get("executor_mode") != "agent_task":
        raise ValueError(f"run '{run_id}' was not created by an agent-task run and cannot be reused; use a new run id")
    raise ValueError(f"run '{run_id}' already exists; agent-task runs cannot be re-run; use a new run id")
