"""Platform admission checks for the local Python isolation boundary."""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable
from typing import Any, cast


def _native_thread_count() -> int | None:
    """Return the OS task/thread count, including threads unknown to Python."""
    if sys.platform.startswith("linux"):
        try:
            with os.scandir("/proc/self/task") as entries:
                count = sum(1 for entry in entries if entry.name.isdigit())
        except OSError:
            return None
        return count if count > 0 else None
    if sys.platform == "darwin":
        return _darwin_native_thread_count()
    return None

def _darwin_native_thread_count() -> int | None:
    """Count Mach task threads and release the kernel-allocated port array."""
    try:
        libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        task_port = ctypes.c_uint.in_dll(libsystem, "mach_task_self_").value
        thread_ports = ctypes.POINTER(ctypes.c_uint)()
        count = ctypes.c_uint()
        libsystem.task_threads.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint)),
            ctypes.POINTER(ctypes.c_uint),
        ]
        libsystem.task_threads.restype = ctypes.c_int
        if libsystem.task_threads(task_port, ctypes.byref(thread_ports), ctypes.byref(count)) != 0:
            return None
        try:
            return count.value if count.value > 0 else None
        finally:
            if not _release_darwin_thread_ports(
                libsystem,
                task_port,
                thread_ports,
                count.value,
            ):
                raise OSError("Mach thread-count resource cleanup failed")
    except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
        return None


def _release_darwin_thread_ports(
    libsystem: Any,
    task_port: int,
    thread_ports: Any,
    count: int,
) -> bool:
    """Release every Mach send right and the kernel-allocated name array."""
    cleanup_succeeded = True
    try:
        libsystem.mach_port_deallocate.argtypes = [ctypes.c_uint, ctypes.c_uint]
        libsystem.mach_port_deallocate.restype = ctypes.c_int
    except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
        cleanup_succeeded = False
    try:
        for index in range(count):
            try:
                result = libsystem.mach_port_deallocate(
                    task_port,
                    thread_ports[index],
                )
            except (
                AttributeError,
                OSError,
                TypeError,
                ValueError,
                ctypes.ArgumentError,
            ):
                cleanup_succeeded = False
                continue
            if result != 0:
                cleanup_succeeded = False
    finally:
        try:
            address = ctypes.cast(thread_ports, ctypes.c_void_p).value
        except (OSError, TypeError, ValueError, ctypes.ArgumentError):
            address = None
            cleanup_succeeded = False
        if address is not None:
            try:
                libsystem.vm_deallocate.argtypes = [
                    ctypes.c_uint,
                    ctypes.c_size_t,
                    ctypes.c_size_t,
                ]
                libsystem.vm_deallocate.restype = ctypes.c_int
            except (
                AttributeError,
                OSError,
                TypeError,
                ValueError,
                ctypes.ArgumentError,
            ):
                cleanup_succeeded = False
            try:
                result = libsystem.vm_deallocate(
                    task_port,
                    address,
                    count * ctypes.sizeof(ctypes.c_uint),
                )
            except (
                AttributeError,
                OSError,
                TypeError,
                ValueError,
                ctypes.ArgumentError,
            ):
                cleanup_succeeded = False
            else:
                if result != 0:
                    cleanup_succeeded = False
        else:
            # A non-empty task_threads result must have array storage to free.
            if count:
                cleanup_succeeded = False
    return cleanup_succeeded


def _safe_unprivileged_uid() -> int | None:
    """Return the stable real UID used by process-count limits, or fail closed."""
    if sys.platform.startswith("linux"):
        getresuid = cast(
            Callable[[], tuple[int, int, int]] | None,
            getattr(os, "getresuid", None),
        )
        if getresuid is None:
            return None
        try:
            real_uid, effective_uid, saved_uid = getresuid()
        except (OSError, TypeError, ValueError):
            return None
        if real_uid == effective_uid == saved_uid and real_uid != 0:
            return real_uid
        return None
    if sys.platform == "darwin":
        try:
            real_uid = os.getuid()
            effective_uid = os.geteuid()
            libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            libsystem.issetugid.argtypes = []
            libsystem.issetugid.restype = ctypes.c_int
            identity_was_tainted = libsystem.issetugid()
        except (
            AttributeError,
            OSError,
            TypeError,
            ValueError,
            ctypes.ArgumentError,
        ):
            return None
        if real_uid == effective_uid and real_uid != 0 and identity_was_tainted == 0:
            return real_uid
    return None
