"""AC-764 — Pi CLI process-group timeout cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from autocontext.runtimes.pi_cli import PiCLIConfig, PiCLIRuntime, _run_with_group_kill


def test_invoke_uses_group_kill_helper_with_timeout_and_workspace() -> None:
    runtime = PiCLIRuntime(PiCLIConfig(timeout=7.0, workspace="/tmp/pi-ws"))
    mock_result = subprocess.CompletedProcess(args=["pi"], returncode=0, stdout="plain output", stderr="")

    with patch("autocontext.runtimes.pi_cli._run_with_group_kill", return_value=mock_result) as mock_run:
        output = runtime.generate("test prompt")

    assert output.text == "plain output"
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["timeout"] == 7.0
    assert mock_run.call_args.kwargs["cwd"] == "/tmp/pi-ws"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups are not available")
def test_run_with_group_kill_kills_process_group_on_timeout() -> None:
    recorded: dict[str, object] = {}

    class _StuckProc:
        pid = 9999

        def __init__(self) -> None:
            self.stdin = None
            self.stdout = None
            self.stderr = None
            self.returncode: int | None = None

        def communicate(self, timeout=None):  # noqa: ANN001, A002
            raise subprocess.TimeoutExpired(cmd=["pi"], timeout=timeout or 0)

        def kill(self) -> None:
            recorded["kill_called"] = True

    def _fake_popen(args: list[str], **kwargs: object) -> _StuckProc:
        recorded["popen_args"] = args
        recorded["popen_kwargs"] = kwargs
        return _StuckProc()

    def _fake_killpg(pgid: int, sig: int) -> None:
        recorded["killpg"] = (pgid, sig)

    with (
        patch("subprocess.Popen", side_effect=_fake_popen),
        patch("os.getpgid", return_value=9999),
        patch("os.killpg", side_effect=_fake_killpg),
        pytest.raises(subprocess.TimeoutExpired),
    ):
        _run_with_group_kill(["pi", "--print", "probe"], timeout=0.1, grace_seconds=0.1)

    popen_kwargs = recorded["popen_kwargs"]
    assert isinstance(popen_kwargs, dict)
    assert popen_kwargs["start_new_session"] is True
    assert recorded["killpg"] == (9999, signal.SIGKILL)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups are not available")
def test_run_with_group_kill_cleans_up_on_keyboard_interrupt() -> None:
    recorded: dict[str, object] = {}

    class _InterruptingProc:
        pid = 9998

        def __init__(self) -> None:
            self.stdin = None
            self.stdout = None
            self.stderr = None
            self.returncode: int | None = None
            self._communicate_call = 0

        def communicate(self, timeout=None):  # noqa: ANN001, A002
            del timeout
            self._communicate_call += 1
            if self._communicate_call == 1:
                raise KeyboardInterrupt
            return ("", "")

        def kill(self) -> None:
            recorded["kill_called"] = True

    def _fake_popen(args: list[str], **kwargs: object) -> _InterruptingProc:
        recorded["popen_args"] = args
        recorded["popen_kwargs"] = kwargs
        return _InterruptingProc()

    def _fake_killpg(pgid: int, sig: int) -> None:
        recorded["killpg"] = (pgid, sig)

    with (
        patch("subprocess.Popen", side_effect=_fake_popen),
        patch("os.getpgid", return_value=9998),
        patch("os.killpg", side_effect=_fake_killpg),
        pytest.raises(KeyboardInterrupt),
    ):
        _run_with_group_kill(["pi", "--print", "probe"], timeout=0.1, grace_seconds=0.1)

    popen_kwargs = recorded["popen_kwargs"]
    assert isinstance(popen_kwargs, dict)
    assert popen_kwargs["start_new_session"] is True
    assert recorded["killpg"] == (9998, signal.SIGKILL)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups are not available")
def test_run_with_group_kill_returns_promptly_when_escaped_descendant_keeps_pipe_open() -> None:
    with tempfile.TemporaryDirectory(prefix="pi-cli-pipe-leak-") as tmp:
        pid_file = Path(tmp) / "escaped-child.pid"
        escaped_child_pid: int | None = None
        parent_code = r"""
import subprocess
import sys
import time

pid_file = sys.argv[1]
grandchild_code = r'''
import os
import sys
import time
from pathlib import Path

os.setsid()
Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
print("escaped-child-start", flush=True)
time.sleep(5)
'''
subprocess.Popen(
    [sys.executable, "-c", grandchild_code, pid_file],
    stdout=sys.stdout,
    stderr=sys.stderr,
    close_fds=False,
)
print("parent-start", flush=True)
time.sleep(30)
"""

        try:
            proc_args = [sys.executable, "-c", parent_code, str(pid_file)]
            started = time.monotonic()
            with pytest.raises(subprocess.TimeoutExpired):
                _run_with_group_kill(proc_args, timeout=0.2, grace_seconds=0.2)
            elapsed = time.monotonic() - started

            if pid_file.exists():
                escaped_child_pid = int(pid_file.read_text(encoding="utf-8"))
            assert elapsed < 2.0, f"timeout cleanup blocked on leaked pipe for {elapsed:.2f}s"
        finally:
            if escaped_child_pid is None:
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline and not pid_file.exists():
                    time.sleep(0.01)
                if pid_file.exists():
                    escaped_child_pid = int(pid_file.read_text(encoding="utf-8"))
            if escaped_child_pid is not None:
                try:
                    os.kill(escaped_child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
