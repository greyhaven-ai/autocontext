"""Spawn-process startup with exclusive wait ownership."""

from __future__ import annotations

import multiprocessing
import os
from multiprocessing.process import BaseProcess
from typing import Any


def start_owned_process(process: Any) -> None:
    """Start a child without enrolling it in multiprocessing's global reaper.

    ``BaseProcess.start()`` registers every child in a process-global set. Any
    later ``Process.start()`` or ``multiprocessing.active_children()`` polls
    that set and could reap a security-sensitive leader before its owner has
    cleaned up the leader's process group. Perform the stable portion of
    ``BaseProcess.start()`` while retaining sole wait ownership instead.

    The fallback preserves lightweight process fakes used by lifecycle tests.
    Production spawn contexts always return a ``BaseProcess`` instance.
    """
    if not isinstance(process, BaseProcess):
        process.start()
        return

    base_process: Any = process
    base_process._check_closed()
    if base_process._popen is not None:
        raise AssertionError("cannot start a process twice")
    if base_process._parent_pid != os.getpid():
        raise AssertionError("can only start a process object created by current process")
    if multiprocessing.current_process().daemon:
        raise AssertionError("daemonic processes are not allowed to have children")
    base_process._popen = base_process._Popen(base_process)
    base_process._sentinel = base_process._popen.sentinel
    # Match BaseProcess.start(): these are no longer needed after spawn and can
    # otherwise form a reference cycle through the target callable.
    del base_process._target, base_process._args, base_process._kwargs


__all__ = ["start_owned_process"]
