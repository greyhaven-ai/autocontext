"""Interpreter-hook handling for the local isolation fork boundary."""
from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from typing import Any

_InterpreterHooks = tuple[Any, Any, Any, Any]


def clear_inherited_interpreter_hooks() -> None:
    """Remove parent tracing/profiling callbacks copied across ``fork``."""
    sys.settrace(None)
    sys.setprofile(None)
    threading.settrace(None)
    threading.setprofile(None)


def restore_interpreter_hooks(hooks: _InterpreterHooks) -> None:
    """Restore trace/profile callbacks after a parent-side fork attempt."""
    sys_trace, sys_profile, thread_trace, thread_profile = hooks
    threading.setprofile(thread_profile)
    threading.settrace(thread_trace)
    sys.setprofile(sys_profile)
    sys.settrace(sys_trace)


def fork_with_clean_interpreter_hooks(
    *,
    native_thread_count: Callable[[], int | None],
    sigchld_disposition_is_safe: Callable[[], bool],
    child_ownership_primitives_available: Callable[[], bool],
    terminate_process_tree: Callable[[int], None],
    unavailable_error: type[Exception],
) -> int:
    """Fork only after suppressing callbacks and rechecking parent invariants."""
    hooks: _InterpreterHooks = (
        sys.gettrace(),
        sys.getprofile(),
        threading.gettrace(),
        threading.getprofile(),
    )
    try:
        clear_inherited_interpreter_hooks()
    except BaseException as exc:
        try:
            restore_interpreter_hooks(hooks)
        except BaseException as restore_exc:
            raise unavailable_error(
                "unable to disable or restore interpreter hooks before fork"
            ) from restore_exc
        raise unavailable_error("unable to disable interpreter hooks before fork") from exc
    try:
        # Recheck immediately adjacent to fork and after disabling callbacks
        # that could mutate these preconditions. Native pthreads created via
        # extensions/ctypes are invisible to ``threading.active_count()``;
        # CPython's post-fork warning is diagnostic only and explicitly clears
        # warning exceptions, so it cannot enforce this boundary.
        if native_thread_count() != 1:
            raise unavailable_error(
                "native thread state changed before isolated child startup"
            )
        if not sigchld_disposition_is_safe():
            raise unavailable_error(
                "SIGCHLD disposition changed before isolated child startup"
            )
        if not child_ownership_primitives_available():
            raise unavailable_error(
                "child ownership primitives changed before isolated child startup"
            )
        pid = os.fork()
    except BaseException:
        try:
            restore_interpreter_hooks(hooks)
        except BaseException as restore_exc:
            raise unavailable_error(
                "unable to restore interpreter hooks after fork failure"
            ) from restore_exc
        raise
    if pid != 0:
        try:
            restore_interpreter_hooks(hooks)
        except BaseException as exc:
            try:
                terminate_process_tree(pid)
            except BaseException as cleanup_exc:
                raise unavailable_error(
                    "unable to restore interpreter hooks or clean up the child"
                ) from cleanup_exc
            raise unavailable_error(
                "unable to restore interpreter hooks after fork; child terminated"
            ) from exc
    return pid
