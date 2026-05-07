"""Run-scoped runtime-session recording helpers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocontext.session.runtime_events import RuntimeSessionEventStore
from autocontext.session.runtime_session import RuntimeSession
from autocontext.session.runtime_session_ids import runtime_session_id_for_run


@dataclass(frozen=True)
class RuntimeSessionRunRecording:
    """Opened runtime-session recording resources for one autocontext run."""

    session: RuntimeSession
    event_store: RuntimeSessionEventStore

    def close(self) -> None:
        self.event_store.close()


def create_runtime_session_for_run(
    *,
    db_path: Path | str,
    run_id: str,
    scenario_name: str = "",
    goal: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> RuntimeSessionRunRecording:
    """Load or create the run-scoped runtime session for provider-runtime recording."""
    store = RuntimeSessionEventStore(db_path)
    session_id = runtime_session_id_for_run(run_id)
    session = RuntimeSession.load(session_id=session_id, event_store=store)
    if session is None:
        session = RuntimeSession.create(
            session_id=session_id,
            goal=goal or _runtime_session_goal(run_id, scenario_name),
            event_store=store,
            metadata={
                "runId": run_id,
                "scenario": scenario_name,
                "source": "python",
                **{str(key): value for key, value in dict(metadata or {}).items()},
            },
        )
    return RuntimeSessionRunRecording(session=session, event_store=store)


@contextmanager
def open_runtime_session_for_run(
    *,
    db_path: Path | str,
    run_id: str,
    scenario_name: str = "",
    goal: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[RuntimeSessionRunRecording]:
    recording = create_runtime_session_for_run(
        db_path=db_path,
        run_id=run_id,
        scenario_name=scenario_name,
        goal=goal,
        metadata=metadata,
    )
    try:
        yield recording
    finally:
        recording.close()


def _runtime_session_goal(run_id: str, scenario_name: str) -> str:
    return f"autoctx run {scenario_name} ({run_id})" if scenario_name else f"autoctx run {run_id}"
