"""AC-570 / AC-588 — claude-cli timeout defaults and observability.

Pins the per-call default (AC-570 raised 120→300; AC-588 raised 300→600 after
the 0.4.5 escalation sweep showed long scenarios still hitting the cap) and
the existing override paths (--timeout flag, AUTOCONTEXT_CLAUDE_TIMEOUT env var).
"""

from __future__ import annotations

import logging
import subprocess
from unittest.mock import patch

import pytest

from autocontext.config.settings import AppSettings, load_settings
from autocontext.runtimes.claude_cli import ClaudeCLIConfig, ClaudeCLIRuntime


class TestClaudeTimeoutDefaults:
    def test_app_settings_claude_timeout_default_is_600s(self) -> None:
        # AC-588: raised 300→600 after the 0.4.5 escalation sweep showed long
        # designer/judge calls exceeding 300s on complex scenarios.
        settings = AppSettings()
        assert settings.claude_timeout == 600.0

    def test_claude_cli_config_default_is_600s(self) -> None:
        cfg = ClaudeCLIConfig()
        assert cfg.timeout == 600.0

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AUTOCONTEXT_CLAUDE_TIMEOUT has always overridden the default; pin it."""
        monkeypatch.setenv("AUTOCONTEXT_CLAUDE_TIMEOUT", "45")

        settings = load_settings()

        assert settings.claude_timeout == 45.0

    def test_cli_timeout_flag_overrides_default_for_claude_cli(self) -> None:
        """--timeout flag routes through apply_judge_runtime_overrides and wins
        over the default for CLI-backed providers."""
        from autocontext.cli_runtime_overrides import apply_judge_runtime_overrides

        base = AppSettings()  # claude_timeout defaults to 600 (AC-588)
        resolved = apply_judge_runtime_overrides(base, provider_name="claude-cli", timeout=90.0)

        assert resolved.claude_timeout == 90.0


class TestClaudeCLIHardKillOnTimeout:
    """AC-761 / AC-735: a stuck claude subprocess must be hard-killed at
    its process group, not just SIGTERM-ed. claude-cli is a Node script
    that spawns helper processes; SIGKILL of the immediate child leaves
    pipe fds open in grandchildren and `subprocess.run`'s drain blocks
    indefinitely, so a 1200s `--timeout` ends up running for hours.

    The runtime must:
      1. Spawn claude in its own session/process group.
      2. On per-call timeout, kill the whole process group (SIGKILL).
      3. Bound the drain phase so a hung pipe reader can't extend the
         wall-clock past `claude_timeout` by more than a small grace.
    """

    def _stuck_popen_factory(self, recorded: dict):
        """Build a fake Popen class whose communicate() always raises
        TimeoutExpired. Records the kill calls so the test can assert
        that the runtime escalated to the process group, not just the
        immediate child."""
        import os
        import signal

        class _StuckProc:
            pid = 9999

            def __init__(self) -> None:
                self.stdin = None
                self.stdout = None
                self.stderr = None
                self.returncode: int | None = None
                self._killed = False

            def communicate(self, input=None, timeout=None):  # noqa: A002
                del input
                # First call: simulate a process that ignores SIGTERM and
                # whose helper children keep the pipe drained forever.
                if not self._killed:
                    raise subprocess.TimeoutExpired(cmd=["claude"], timeout=timeout or 0)
                # After kill: drain completes promptly.
                return ("", "")

            def kill(self) -> None:
                self._killed = True
                recorded["kill_called"] = True

            def wait(self, timeout=None) -> int:
                del timeout
                self.returncode = -9
                return -9

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                # Defensive: if the runtime wraps Popen in `with`, drain
                # path on __exit__ must not block. The fake just no-ops.
                return None

        def _fake_popen(args, **kwargs):
            recorded["popen_kwargs"] = kwargs
            return _StuckProc()

        def _fake_killpg(pgid, sig):
            recorded["killpg"] = (pgid, sig)
            recorded.setdefault("killpg_sig", sig)

        def _fake_getpgid(pid):
            return pid

        return _fake_popen, _fake_killpg, _fake_getpgid, signal.SIGKILL, os

    def test_runtime_kills_process_group_on_timeout(self) -> None:
        import signal

        recorded: dict = {}
        popen, killpg, getpgid, sigkill, _os = self._stuck_popen_factory(recorded)

        runtime = ClaudeCLIRuntime(
            ClaudeCLIConfig(
                model="sonnet",
                timeout=1.0,
                max_retries=0,
                retry_backoff_seconds=0.0,
            )
        )

        with (
            patch("subprocess.Popen", side_effect=popen),
            patch("os.killpg", side_effect=killpg),
            patch("os.getpgid", side_effect=getpgid),
        ):
            output = runtime.generate(prompt="probe")

        # The runtime must have spawned with `start_new_session=True` so the
        # child became its own process-group leader; the timeout path must
        # have called `os.killpg(pgid, SIGKILL)` to nuke the whole group.
        assert recorded["popen_kwargs"].get("start_new_session") is True, (
            f"expected start_new_session=True, got kwargs={recorded['popen_kwargs']}"
        )
        assert "killpg" in recorded, "runtime did not kill the process group on timeout"
        _pgid, sig = recorded["killpg"]
        assert sig == signal.SIGKILL, f"expected SIGKILL, got {sig}"

        # And the runtime must surface a timeout AgentOutput, not hang.
        assert output.text == ""
        assert output.metadata.get("error") == "timeout"

    def test_runtime_bounded_when_subprocess_ignores_terminate(self) -> None:
        """Wall-clock bound: even when the subprocess ignores graceful
        signals, the runtime must return within ~2 * timeout (timeout +
        bounded drain grace). 1s timeout + 5s grace => must return well
        under 30s real-time; we assert <= 10s with margin for CI noise."""
        import time as _time

        recorded: dict = {}
        popen, killpg, getpgid, _, _ = self._stuck_popen_factory(recorded)

        runtime = ClaudeCLIRuntime(
            ClaudeCLIConfig(
                model="sonnet",
                timeout=1.0,
                max_retries=0,
                retry_backoff_seconds=0.0,
            )
        )

        with (
            patch("subprocess.Popen", side_effect=popen),
            patch("os.killpg", side_effect=killpg),
            patch("os.getpgid", side_effect=getpgid),
        ):
            t0 = _time.monotonic()
            runtime.generate(prompt="probe")
            elapsed = _time.monotonic() - t0

        assert elapsed < 10.0, (
            f"runtime did not return within bounded wall-clock; elapsed={elapsed:.2f}s (claude_timeout=1.0s; bound should be ~6s)"
        )

    def test_helper_kills_process_group_on_keyboard_interrupt(self) -> None:
        """AC-761 PR #940 review P2: because the child is detached
        (`start_new_session=True`), Ctrl-C / SIGINT in the terminal no
        longer reaches the claude process group. If the user interrupts
        during `communicate()`, the detached claude keeps running.

        The helper must kill the process group on any BaseException
        (KeyboardInterrupt, SystemExit, ...) -- not only on
        TimeoutExpired -- and then re-raise.
        """
        import signal

        from autocontext.runtimes.claude_cli import _run_with_group_kill

        recorded: dict = {}

        class _InterruptingProc:
            pid = 9999

            def __init__(self) -> None:
                self.stdin = None
                self.stdout = None
                self.stderr = None
                self.returncode: int | None = None
                self._communicate_call = 0

            def communicate(self, input=None, timeout=None):  # noqa: A002
                del input
                self._communicate_call += 1
                # First call: simulate Ctrl-C during the wait.
                if self._communicate_call == 1:
                    raise KeyboardInterrupt
                # Second call (drain after kill): return promptly.
                return ("", "")

            def wait(self, timeout=None) -> int:  # noqa: ARG002
                self.returncode = -9
                return -9

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        def _fake_popen(args, **kwargs):
            recorded["popen_kwargs"] = kwargs
            return _InterruptingProc()

        def _fake_killpg(pgid, sig):
            recorded["killpg"] = (pgid, sig)

        def _fake_getpgid(pid):
            return pid

        with (
            patch("subprocess.Popen", side_effect=_fake_popen),
            patch("os.killpg", side_effect=_fake_killpg),
            patch("os.getpgid", side_effect=_fake_getpgid),
            pytest.raises(KeyboardInterrupt),
        ):
            _run_with_group_kill(["claude"], prompt="probe", timeout=1.0)

        assert "killpg" in recorded, (
            "process group was not killed on KeyboardInterrupt; detached claude subprocess would have leaked"
        )
        _pgid, sig = recorded["killpg"]
        assert sig == signal.SIGKILL


class TestClaudeCLIRuntimeObservabilityViaHelper:
    """AC-761 PR #940 review P2: the existing observability test patched
    `subprocess.run`, but the runtime no longer calls it. On contributor
    machines with claude installed the patch was ineffective and the
    test could invoke the real claude binary. Patch the helper instead.
    """

    def test_runtime_logs_invoke_via_helper_patch(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        completed = subprocess.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout='{"type":"result","subtype":"success","is_error":false,'
            '"result":"ok","total_cost_usd":0.0,"session_id":"t","duration_ms":1}',
            stderr="",
        )

        runtime = ClaudeCLIRuntime(ClaudeCLIConfig(model="sonnet", timeout=300.0))

        with caplog.at_level(logging.INFO, logger="autocontext.runtimes.claude_cli"):
            with patch(
                "autocontext.runtimes.claude_cli._run_with_group_kill",
                return_value=completed,
            ):
                runtime.generate(prompt="probe")

        invoke_records = [r for r in caplog.records if r.levelno == logging.INFO and "claude-cli invoke" in r.getMessage()]
        assert len(invoke_records) == 1
        message = invoke_records[0].getMessage()
        assert "model=sonnet" in message
        assert "timeout=300s" in message
