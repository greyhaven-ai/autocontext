"""Linux process and seccomp containment for local Python isolation."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any

_FilterRule = tuple[int, int, int, int]


class _LinuxContainmentUnavailable(RuntimeError):
    """A required Linux process-containment primitive could not be enforced."""


def _install_linux_process_group_filter() -> None:
    """Install an inherited seccomp filter denying session/group escape."""
    try:
        import ctypes
        import errno

        machine = os.uname().machine.lower()
        rules = _linux_process_group_filter_rules(machine, errno_value=errno.EPERM)

        class _SockFilter(ctypes.Structure):
            _fields_ = [
                ("code", ctypes.c_ushort),
                ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte),
                ("k", ctypes.c_uint),
            ]

        class _SockFprog(ctypes.Structure):
            _fields_ = [
                ("len", ctypes.c_ushort),
                ("filter", ctypes.POINTER(_SockFilter)),
            ]

        instructions = (_SockFilter * len(rules))(
            *(_SockFilter(code, jump_true, jump_false, value) for code, jump_true, jump_false, value in rules)
        )
        program = _SockFprog(len(instructions), instructions)
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS failed")
        program_address = ctypes.cast(ctypes.pointer(program), ctypes.c_void_p).value
        if program_address is None:
            raise _LinuxContainmentUnavailable("unable to address the Linux seccomp filter")
        if libc.prctl(22, 2, program_address, 0, 0) != 0:  # PR_SET_SECCOMP, FILTER
            raise OSError(ctypes.get_errno(), "PR_SET_SECCOMP failed")
    except _LinuxContainmentUnavailable:
        raise
    except (AttributeError, ImportError, OSError, TypeError, ValueError) as exc:
        raise _LinuxContainmentUnavailable("unable to install Linux process containment") from exc


def _linux_process_group_filter_rules(
    machine: str,
    *,
    errno_value: int,
) -> tuple[_FilterRule, ...]:
    """Return classic-BPF rules with explicit architecture/ABI validation."""
    parameters = {
        # AUDIT_ARCH_* includes the little-endian and 64-bit ABI marker bits.
        # arch, setsid, setpgid, clone, unshare, setns, clone3
        "amd64": (0xC000003E, 112, 109, 56, 272, 308, 435),
        "x86_64": (0xC000003E, 112, 109, 56, 272, 308, 435),
        "aarch64": (0xC00000B7, 157, 154, 220, 97, 268, 435),
        "arm64": (0xC00000B7, 157, 154, 220, 97, 268, 435),
    }.get(machine)
    if parameters is None:
        raise _LinuxContainmentUnavailable("unsupported Linux seccomp architecture")
    (
        audit_arch,
        setsid_number,
        setpgid_number,
        clone_number,
        unshare_number,
        setns_number,
        clone3_number,
    ) = parameters
    load_word_absolute = 0x20  # BPF_LD | BPF_W | BPF_ABS
    jump_if_equal = 0x15  # BPF_JMP | BPF_JEQ | BPF_K
    jump_if_bits_set = 0x45  # BPF_JMP | BPF_JSET | BPF_K
    return_constant = 0x06  # BPF_RET | BPF_K
    bitwise_and = 0x54  # BPF_ALU | BPF_AND | BPF_K
    deny_with_errno = 0x00050000 | errno_value  # SECCOMP_RET_ERRNO
    pretend_unimplemented = 0x00050000 | 38  # SECCOMP_RET_ERRNO | ENOSYS
    kill_process = 0x80000000  # SECCOMP_RET_KILL_PROCESS
    allow = 0x7FFF0000  # SECCOMP_RET_ALLOW
    return (
        (load_word_absolute, 0, 0, 4),  # seccomp_data.arch
        (jump_if_equal, 1, 0, audit_arch),
        (return_constant, 0, 0, kill_process),
        (load_word_absolute, 0, 0, 0),  # seccomp_data.nr
        # x86-64's x32 ABI ORs syscall numbers with 0x40000000. Normalize
        # that marker before comparisons so it cannot bypass the filter.
        (bitwise_and, 0, 0, 0xBFFFFFFF),
        (jump_if_equal, 0, 1, setsid_number),
        (return_constant, 0, 0, deny_with_errno),
        (jump_if_equal, 0, 1, setpgid_number),
        (return_constant, 0, 0, deny_with_errno),
        (jump_if_equal, 0, 1, unshare_number),
        (return_constant, 0, 0, deny_with_errno),
        (jump_if_equal, 0, 1, setns_number),
        (return_constant, 0, 0, deny_with_errno),
        # clone3 hides flags behind a user pointer that classic BPF cannot
        # dereference. ENOSYS makes pthread/glibc safely retry ordinary clone.
        (jump_if_equal, 0, 1, clone3_number),
        (return_constant, 0, 0, pretend_unimplemented),
        (jump_if_equal, 0, 3, clone_number),
        (load_word_absolute, 0, 0, 16),  # seccomp_data.args[0] low word
        (jump_if_bits_set, 0, 1, 0x10000000),  # CLONE_NEWUSER
        (return_constant, 0, 0, deny_with_errno),
        (return_constant, 0, 0, allow),
    )


def _apply_linux_process_limit(
    resource_module: Any,
    *,
    max_child_tasks: int,
    safe_unprivileged_uid: Callable[[], int | None],
    capability_masks: Callable[[], tuple[int, int, int, int] | None],
    same_uid_task_count: Callable[[], int | None],
) -> None:
    """Reserve a bounded number of new same-UID tasks without a fixed UID cap."""
    if not sys.platform.startswith("linux"):
        return
    process_limit = getattr(resource_module, "RLIMIT_NPROC", None)
    if process_limit is None:
        raise _LinuxContainmentUnavailable("Linux process-count limits are unavailable")
    if safe_unprivileged_uid() is None:
        raise _LinuxContainmentUnavailable("Linux process-count limits require matching non-root UID identities")
    current_capability_masks = capability_masks()
    if current_capability_masks is None:
        raise _LinuxContainmentUnavailable("unable to verify Linux capability masks")
    if any(current_capability_masks):
        raise _LinuxContainmentUnavailable("Linux process containment requires empty capability masks")
    baseline = same_uid_task_count()
    if baseline is None:
        raise _LinuxContainmentUnavailable("unable to establish the Linux same-UID task baseline")

    try:
        soft, hard = resource_module.getrlimit(process_limit)
        target = baseline + max_child_tasks
        if hard != resource_module.RLIM_INFINITY:
            target = min(target, hard)
        if soft != resource_module.RLIM_INFINITY:
            target = min(target, soft)
        if target <= baseline:
            raise _LinuxContainmentUnavailable("the Linux process limit has no safe child allowance")
        resource_module.setrlimit(process_limit, (target, target))
        applied_soft, applied_hard = resource_module.getrlimit(process_limit)
    except _LinuxContainmentUnavailable:
        raise
    except (OSError, ValueError) as exc:
        raise _LinuxContainmentUnavailable("unable to enforce the Linux process-count limit") from exc
    if applied_soft != target or applied_hard != target:
        raise _LinuxContainmentUnavailable("unable to verify the Linux process-count limit")


def _linux_capability_masks() -> tuple[int, int, int, int] | None:
    """Read inheritable/permitted/effective/ambient Linux capability masks."""
    try:
        values: dict[str, int] = {}
        with open("/proc/self/status", encoding="ascii") as status_file:
            for line in status_file:
                key, separator, value = line.partition(":")
                if separator and key in {"CapInh", "CapPrm", "CapEff", "CapAmb"}:
                    values[key] = int(value.strip(), 16)
        if values.keys() != {"CapInh", "CapPrm", "CapEff", "CapAmb"}:
            return None
        return (
            values["CapInh"],
            values["CapPrm"],
            values["CapEff"],
            values["CapAmb"],
        )
    except (OSError, UnicodeError, ValueError):
        return None


def _linux_same_uid_task_count(
    safe_unprivileged_uid: Callable[[], int | None],
) -> int | None:
    """Count current Linux tasks for the real UID, or return None if uncertain."""
    try:
        real_uid = safe_unprivileged_uid()
        if real_uid is None:
            return None
        process_entries = os.scandir("/proc")
    except (AttributeError, OSError):
        return None

    total = 0
    with process_entries:
        for process_entry in process_entries:
            if not process_entry.name.isdigit():
                continue
            try:
                with open(f"{process_entry.path}/status", encoding="ascii") as status_file:
                    uid_line = next((line for line in status_file if line.startswith("Uid:")), None)
                if uid_line is None:
                    return None
                uid_fields = uid_line.split()
                if len(uid_fields) < 2 or int(uid_fields[1]) != real_uid:
                    continue
                with os.scandir(f"{process_entry.path}/task") as task_entries:
                    total += sum(1 for entry in task_entries if entry.name.isdigit())
            except FileNotFoundError:
                # Processes may exit between /proc enumeration and inspection.
                continue
            except (OSError, UnicodeError, ValueError):
                return None
    return total if total > 0 else None
