"""Ownership-safe process-group signaling shared by local boundaries."""

from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Callable
from typing import Any, Literal

ProcessState = Literal["running", "exited", "ownership_lost"]

_DARWIN_EXIT_OBSERVATION_GRACE_SECONDS = 0.02
_DARWIN_EXIT_OBSERVATION_POLL_SECONDS = 0.001


def signal_owned_process_group(pid: int, signum: int | signal.Signals) -> bool:
    """Signal an owned group, tolerating Darwin's zombie-only ``EPERM``."""
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Darwin returns EPERM for a group containing only its unreaped zombie
        # leader. A live same-credential member makes killpg succeed; callers
        # establish that identity invariant (and isolation forbids descendants).
        if sys.platform != "darwin" or not _darwin_leader_is_exited(pid):
            raise
        return False
    return True


def process_state_without_reaping(
    process: Any,
    wait_for_connections: Callable[..., list[Any]],
) -> ProcessState:
    """Observe a leader without consuming its wait status on POSIX."""
    try:
        pid = process.pid
    except ValueError:
        return "ownership_lost"
    if pid is None:
        return "ownership_lost"
    if os.name == "posix":
        waitid: Any = getattr(os, "waitid", None)
        process_id_type: Any = getattr(os, "P_PID", None)
        no_wait: Any = getattr(os, "WNOWAIT", None)
        exited: Any = getattr(os, "WEXITED", None)
        no_hang: Any = getattr(os, "WNOHANG", None)
        if any(value is None for value in (waitid, process_id_type, no_wait, exited, no_hang)):
            return "ownership_lost"
        try:
            info = waitid(process_id_type, pid, exited | no_hang | no_wait)
        except (ChildProcessError, OSError, TypeError, ValueError):
            return "ownership_lost"
        return "exited" if info is not None else "running"
    try:
        return "exited" if wait_for_connections([process.sentinel], timeout=0) else "running"
    except (OSError, ValueError):
        return "ownership_lost"


def _darwin_leader_is_exited(pid: int) -> bool:
    """Recheck that an unreaped Darwin leader is a zombie after ``EPERM``.

    Darwin can report ``EPERM`` while a process group is transitioning to a
    zombie a fraction of a millisecond before ``waitid`` exposes that exit.
    Poll only for a short bounded grace and tolerate the denial solely after
    positive child-exit proof.
    """
    waitid: Any = getattr(os, "waitid", None)
    process_id_type: Any = getattr(os, "P_PID", None)
    no_wait: Any = getattr(os, "WNOWAIT", None)
    exited: Any = getattr(os, "WEXITED", None)
    no_hang: Any = getattr(os, "WNOHANG", None)
    if any(value is None for value in (waitid, process_id_type, no_wait, exited, no_hang)):
        return False
    deadline = time.monotonic() + _DARWIN_EXIT_OBSERVATION_GRACE_SECONDS
    while True:
        try:
            info = waitid(process_id_type, pid, exited | no_hang | no_wait)
        except (ChildProcessError, OSError, TypeError, ValueError):
            return False
        if info is not None:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_DARWIN_EXIT_OBSERVATION_POLL_SECONDS, remaining))
