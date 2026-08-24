"""Cross-platform advisory locks for durable local stores."""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import local
from typing import Protocol, cast


class _WindowsLocking(Protocol):
    LK_LOCK: int
    LK_UNLCK: int

    def locking(self, fd: int, mode: int, nbytes: int, /) -> None: ...


class _PosixLocking(Protocol):
    LOCK_EX: int
    LOCK_UN: int

    def flock(self, fd: int, operation: int, /) -> None: ...


class _AdvisoryLockState(local):
    def __init__(self) -> None:
        self.paths: set[str] = set()


_ADVISORY_LOCK_STATE = _AdvisoryLockState()


def _windows_locking() -> _WindowsLocking:
    return cast(_WindowsLocking, importlib.import_module("msvcrt"))


def _posix_locking() -> _PosixLocking:
    return cast(_PosixLocking, importlib.import_module("fcntl"))


def _lock(fd: int) -> None:
    if os.name == "nt":
        msvcrt = _windows_locking()
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        return

    fcntl = _posix_locking()
    fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        msvcrt = _windows_locking()
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return

    fcntl = _posix_locking()
    fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def advisory_path_lock(path: Path, mode: int = 0o600) -> Iterator[None]:
    """Hold an exclusive advisory lock associated with ``path``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.abspath(path)
    if key in _ADVISORY_LOCK_STATE.paths:
        yield
        return
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, mode)
    locked = False
    try:
        _lock(fd)
        locked = True
        _ADVISORY_LOCK_STATE.paths.add(key)
        yield
    finally:
        try:
            if locked:
                try:
                    _unlock(fd)
                finally:
                    _ADVISORY_LOCK_STATE.paths.discard(key)
        finally:
            os.close(fd)


@contextmanager
def locked_append_fd(path: Path, mode: int = 0o644) -> Iterator[int]:
    """Open ``path`` for append and hold its platform advisory lock."""

    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, mode)
    locked = False
    try:
        _lock(fd)
        locked = True
        yield fd
    finally:
        try:
            if locked:
                _unlock(fd)
        finally:
            os.close(fd)


def append_bytes_locked(path: Path, payload: bytes, mode: int = 0o644) -> None:
    """Append one payload under an inter-process lock and durably sync it."""

    with locked_append_fd(path, mode) as fd:
        with os.fdopen(fd, "ab", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(fd)


__all__ = ["advisory_path_lock", "append_bytes_locked", "locked_append_fd"]
