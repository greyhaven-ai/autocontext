"""Protected PID-1 supervisor, copied into the isolated skill container."""

from __future__ import annotations

import ctypes
import json
import os
import signal
import sys
import time
from pathlib import Path

OOM_EXIT = 123
UNVERIFIED_EXIT = 125


def disable_ptrace() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def read_oom_kills(memory_bytes: int) -> int:
    # Require our private cgroup-v2 root, with the exact requested memory cap.
    # Never mistake an ancestor/host counter or an unsupported runtime for zero.
    if Path("/proc/self/cgroup").read_text() != "0::/\n":
        raise ValueError("unverified cgroup namespace")
    root = Path("/sys/fs/cgroup")
    if (root / "memory.max").read_text().strip() != str(memory_bytes):
        raise ValueError("unverified cgroup memory limit")
    data = (root / "memory.events").read_text()
    if len(data) > 4096:
        raise ValueError("oversized cgroup counter")
    counters: dict[str, int] = {}
    for line in data.splitlines():
        key, value = line.split()
        if key in counters or not value.isascii() or not value.isdecimal():
            raise ValueError("malformed cgroup counter")
        counters[key] = int(value)
    if "oom_kill" not in counters:
        raise ValueError("missing cgroup OOM counter")
    return counters["oom_kill"]


def quiesce_descendants() -> bool:
    if os.getpid() != 1:
        return False
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:
            pass
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
        if not any(name.isdecimal() and name != "1" for name in os.listdir("/proc")):
            return True
        time.sleep(0.01)
    return False


def main(memory_bytes: int) -> int:
    if os.getpid() != 1:
        return UNVERIFIED_EXIT
    try:
        disable_ptrace()
        if read_oom_kills(memory_bytes):
            return OOM_EXIT
        pid = os.fork()
    except (OSError, ValueError):
        return UNVERIFIED_EXIT
    if pid == 0:
        namespace: dict = {}
        source = Path("/input/skill.py").read_text()
        state = json.loads(Path("/input/input.json").read_text())
        exec(compile(source, "/input/skill.py", "exec"), namespace)  # noqa: S102
        result = namespace["choose_action"](state)
        sys.stdout.write(json.dumps(result, allow_nan=False, separators=(",", ":")))
        sys.stdout.flush()
        os._exit(0)

    try:
        _, status, usage = os.wait4(pid, 0)
        # Child output and metrics are untrusted. Only this protected parent's
        # exit status can attest to the kernel counter after all code stops.
        if not quiesce_descendants():
            return UNVERIFIED_EXIT
        if read_oom_kills(memory_bytes):
            return OOM_EXIT
        sys.stderr.write("AC_RESOURCE_METRICS:" + json.dumps({
            "cpu_seconds": usage.ru_utime + usage.ru_stime,
            "peak_memory_bytes": usage.ru_maxrss * 1024,
        }) + "\n")
        sys.stderr.flush()
        return 0 if os.waitstatus_to_exitcode(status) == 0 else 1
    except (OSError, ValueError):
        return UNVERIFIED_EXIT


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
