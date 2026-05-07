"""Runtime-session recording glue for Python role execution."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from autocontext.agents.provider_bridge import wrap_runtime_session_client
from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.session.runtime_session_recording import open_runtime_session_for_run


@contextmanager
def run_runtime_session_scope(
    orchestrator: Any,
    *,
    run_id: str,
    scenario_name: str,
) -> Iterator[None]:
    """Attach a run-scoped runtime session to an orchestrator while a run stage executes."""
    if not run_id:
        yield
        return
    db_path = getattr(getattr(orchestrator, "settings", None), "db_path", None)
    if not isinstance(db_path, (str, Path)):
        yield
        return
    previous_session = getattr(orchestrator, "_active_runtime_session", None)
    with open_runtime_session_for_run(
        db_path=db_path,
        run_id=run_id,
        scenario_name=scenario_name,
    ) as recording:
        orchestrator._active_runtime_session = recording.session
        try:
            yield
        finally:
            orchestrator._active_runtime_session = previous_session


def runtime_session_client_for_role(
    orchestrator: Any,
    client: LanguageModelClient,
    role: str,
) -> LanguageModelClient:
    session = getattr(orchestrator, "_active_runtime_session", None)
    if session is None:
        return client
    return wrap_runtime_session_client(client, session=session, role=role, cwd=str(Path.cwd()))
