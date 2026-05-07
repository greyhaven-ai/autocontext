from __future__ import annotations


def runtime_session_id_for_run(run_id: str) -> str:
    """Return the persisted runtime-session id for an AutoContext run."""
    return f"run:{run_id}:runtime"
