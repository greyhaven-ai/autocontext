"""Acceptance for the actual trusted CPU worker's OS resource boundary."""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_runner, cpu_worker

resource = pytest.importorskip("resource")


@pytest.mark.parametrize("memory,cpu", [(255, 1), (2049, 1), (256, 0), (256, 31),
                                       (True, 1), (256, True), (256.0, 1), (256, 1.0), ("256", 1)])
def test_invalid_limits_do_not_change_process_limits(
    monkeypatch: pytest.MonkeyPatch, memory: Any, cpu: Any,
) -> None:
    monkeypatch.setattr(resource, "setrlimit", lambda *args: pytest.fail("invalid limits installed"))
    with pytest.raises(ValueError, match="outside the supported bounds"):
        cpu_worker._install_resource_limits(memory, cpu)


def test_unsupported_limit_host_fails_before_installation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cpu_worker.sys, "platform", "unsupported")
    monkeypatch.setattr(resource, "setrlimit", lambda *args: pytest.fail("unsupported limits installed"))
    with pytest.raises(ValueError, match="supported POSIX"):
        cpu_worker._install_resource_limits(256, 1)


@pytest.mark.parametrize("mismatch", [None, "as", "cpu", "core"])
def test_limit_installation_verifies_all_actual_bounds(monkeypatch: pytest.MonkeyPatch, mismatch: str | None) -> None:
    installed: dict[int, tuple[int, int]] = {}
    monkeypatch.setattr(cpu_worker.sys, "platform", "linux")
    monkeypatch.setattr(resource, "setrlimit", lambda key, limits: installed.__setitem__(key, limits))
    ids = {"as": resource.RLIMIT_AS, "cpu": resource.RLIMIT_CPU, "core": resource.RLIMIT_CORE}

    def actual(key: int) -> tuple[int, int]:
        limits = installed[key]
        return (limits[0], limits[1] + 1) if mismatch and ids[mismatch] == key else limits

    monkeypatch.setattr(resource, "getrlimit", actual)
    if mismatch:
        with pytest.raises(ValueError, match="not installed"):
            cpu_worker._install_resource_limits(256, 1)
    else:
        assert cpu_worker._install_resource_limits(256, 1) == 256 * 1024 * 1024
        assert installed == {resource.RLIMIT_AS: (256 * 1024 * 1024,) * 2,
                             resource.RLIMIT_CPU: (1, 1), resource.RLIMIT_CORE: (0, 0)}


def _probe_source(body: str, *, cpu_seconds: int = 1) -> str:
    # Loading the worker file directly keeps the probe stdlib-only, like the
    # production worker before it installs limits and imports the model.
    path = str(Path(cpu_worker.__file__).resolve())
    return f"""
import importlib.util, json, os, resource, sys, time
spec = importlib.util.spec_from_file_location('trusted_cpu_worker', {path!r})
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
memory = worker._install_resource_limits(256, {cpu_seconds})
print(json.dumps({{'pid': os.getpid(), 'memory': memory,
                  'as': resource.getrlimit(resource.RLIMIT_AS),
                  'cpu': resource.getrlimit(resource.RLIMIT_CPU),
                  'core': resource.getrlimit(resource.RLIMIT_CORE)}}), flush=True)
{body}
"""


def _assert_reaped(pid: int) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


def _check_ready(raw: bytes, *, cpu_seconds: int = 1) -> dict[str, Any]:
    ready: dict[str, Any] = json.loads(raw)
    assert ready["memory"] == 256 * 1024 * 1024
    assert ready["as"] == [ready["memory"], ready["memory"]]
    assert ready["cpu"] == [cpu_seconds, cpu_seconds]
    assert ready["core"] == [0, 0]
    return ready


@pytest.mark.skipif(sys.platform != "linux", reason="Linux RLIMIT_AS allocation denial")
def test_linux_address_space_exhaustion_is_rejected_and_reaped() -> None:
    source = _probe_source("""
try:
    allocation = bytearray(512 * 1024 * 1024)
except MemoryError:
    sys.stderr.write('EXPECTED_AS_ALLOCATION_DENIAL')
    sys.exit(73)
raise AssertionError('allocation exceeded the installed address-space limit')
""")
    raw, error, elapsed, code, _ = cpu_runner._run_bounded(
        [sys.executable, "-I", "-c", source], b"", wall_seconds=10.0,
    )
    ready = _check_ready(raw)
    assert code == 73, (code, error)
    assert error == b"EXPECTED_AS_ALLOCATION_DENIAL"
    assert 0 < elapsed < 10.0
    _assert_reaped(ready["pid"])
    # A caught allocation failure still cannot become a successful receipt.
    with pytest.raises(cpu_runner.CpuRunnerError, match="exited unsuccessfully"):
        cpu_runner._validate_response(raw, (), runtime_digest="0" * 64, elapsed=elapsed, returncode=code)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux RLIMIT_CPU hard-limit termination")
def test_linux_cpu_exhaustion_is_kernel_killed_before_wall_watchdog_and_reaped() -> None:
    source = _probe_source("while True:\n    pass")
    # RLIMIT_CPU is one CPU second. This larger independent wall deadline is
    # only a watchdog: a wall-time kill raises instead of returning SIGKILL.
    raw, error, elapsed, code, _ = cpu_runner._run_bounded(
        [sys.executable, "-I", "-c", source], b"", wall_seconds=10.0,
    )
    ready = _check_ready(raw)
    assert code == -signal.SIGKILL, (code, error)
    assert error == b""
    assert 0 < elapsed < 10.0
    _assert_reaped(ready["pid"])
    with pytest.raises(cpu_runner.CpuRunnerError, match="exited unsuccessfully"):
        cpu_runner._validate_response(raw, (), runtime_digest="0" * 64, elapsed=elapsed, returncode=code)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux worker limits plus parent wall deadline")
def test_linux_wall_deadline_reaps_a_worker_with_installed_limits(tmp_path: Path) -> None:
    marker = tmp_path / "worker.json"
    source = _probe_source(f"""
from pathlib import Path
Path({str(marker)!r}).write_text(json.dumps({{'pid': os.getpid(), 'cpu': resource.getrlimit(resource.RLIMIT_CPU)}}))
time.sleep(30)
""", cpu_seconds=30)
    with pytest.raises(cpu_runner.CpuRunnerError, match="wall deadline"):
        cpu_runner._run_bounded([sys.executable, "-I", "-c", source], b"", wall_seconds=1.0)
    ready = json.loads(marker.read_bytes())
    assert ready["cpu"] == [30, 30]
    _assert_reaped(ready["pid"])
