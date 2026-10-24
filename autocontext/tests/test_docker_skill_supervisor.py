"""Kernel evidence must gate skill output independently of Docker event timing."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from autocontext.execution import docker_skill_supervisor as supervisor


@pytest.mark.parametrize("events,expected", [
    ("low 0\noom 0\noom_kill 0\n", 0),
    ("oom 2\noom_kill 1\n", 1),
    ("oom_kill 2\noom_group_kill 1\n", 2),
])
def test_reads_own_kernel_oom_counter(monkeypatch, events, expected):
    values = {"/proc/self/cgroup": "0::/\n", "/sys/fs/cgroup/memory.max": "134217728\n",
              "/sys/fs/cgroup/memory.events": events}
    monkeypatch.setattr(Path, "read_text", lambda self: values[str(self)])
    assert supervisor.read_oom_kills(128 * 1024 * 1024) == expected


@pytest.mark.parametrize("path,value", [
    ("/proc/self/cgroup", "0::/ancestor/other\n"),
    ("/proc/self/cgroup", "5:memory:/\n"),
    ("/sys/fs/cgroup/memory.max", "max\n"),
    ("/sys/fs/cgroup/memory.max", "268435456\n"),
    ("/sys/fs/cgroup/memory.events", "oom 0\n"),
    ("/sys/fs/cgroup/memory.events", "oom_kill 0\noom_kill 1\n"),
    ("/sys/fs/cgroup/memory.events", "oom_kill -1\n"),
    ("/sys/fs/cgroup/memory.events", "oom_kill ０\n"),
    ("/sys/fs/cgroup/memory.events", "oom_kill 0 extra\n"),
    ("/sys/fs/cgroup/memory.events", "x" * 4097),
])
def test_ambiguous_or_invalid_kernel_evidence_is_rejected(monkeypatch, path, value):
    values = {"/proc/self/cgroup": "0::/\n", "/sys/fs/cgroup/memory.max": "134217728\n",
              "/sys/fs/cgroup/memory.events": "oom_kill 0\n", path: value}
    monkeypatch.setattr(Path, "read_text", lambda self: values[str(self)])
    with pytest.raises(ValueError):
        supervisor.read_oom_kills(128 * 1024 * 1024)


@pytest.mark.parametrize("counter,quiescent,expected", [
    (0, True, 0), (1, True, supervisor.OOM_EXIT),
    (OSError("unreadable"), True, supervisor.UNVERIFIED_EXIT),
    (0, False, supervisor.UNVERIFIED_EXIT),
])
def test_success_requires_kernel_evidence_after_quiescence(monkeypatch, counter, quiescent, expected):
    order = []
    def read(_):
        if not order:
            order.append("initial counter")
            return 0
        assert order == ["initial counter", "wait", "quiesce"]
        if isinstance(counter, Exception):
            raise counter
        return counter
    def wait(_pid, _flags):
        order.append("wait")
        return 2, 0, SimpleNamespace(ru_utime=0, ru_stime=0, ru_maxrss=0)
    def quiesce():
        order.append("quiesce")
        return quiescent
    monkeypatch.setattr(supervisor.os, "getpid", lambda: 1)
    monkeypatch.setattr(supervisor, "disable_ptrace", lambda: None)
    monkeypatch.setattr(supervisor.os, "fork", lambda: 2)
    monkeypatch.setattr(supervisor.os, "wait4", wait)
    monkeypatch.setattr(supervisor, "quiesce_descendants", quiesce)
    monkeypatch.setattr(supervisor, "read_oom_kills", read)
    assert supervisor.main(128 * 1024 * 1024) == expected


def test_supervisor_refuses_to_kill_processes_outside_its_pid_namespace(monkeypatch):
    monkeypatch.setattr(supervisor.os, "getpid", lambda: 42)
    monkeypatch.setattr(supervisor.os, "kill", lambda *_: pytest.fail("must not signal host processes"))
    assert not supervisor.quiesce_descendants()
    assert supervisor.main(128 * 1024 * 1024) == supervisor.UNVERIFIED_EXIT
