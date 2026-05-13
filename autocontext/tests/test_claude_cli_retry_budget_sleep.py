"""AC-735 follow-up — retry sleep must respect the attached RuntimeBudget.

Reviewer P2: after a per-attempt timeout, the retry path computed
``delay`` and slept the full ``time.sleep(delay)`` before re-checking
the budget. With a small attached RuntimeBudget that prior calls had
nearly exhausted, the sleep itself could push the runtime past the
advertised wall-clock cap.

Pin the contract: when the attached budget cannot cover the planned
backoff sleep, the runtime must skip the retry and emit a timeout
result immediately.
"""

from __future__ import annotations

from unittest.mock import patch

from autocontext.runtimes.claude_cli import ClaudeCLIConfig, ClaudeCLIRuntime
from autocontext.runtimes.runtime_budget import RuntimeBudget


class _RecordingSleep:
    """Spy for time.sleep so we can pin "did not sleep" in tests."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(float(seconds))


def _runtime_with_budget(*, budget_seconds: float, retries: int, backoff: float) -> ClaudeCLIRuntime:
    cfg = ClaudeCLIConfig(
        max_retries=retries,
        retry_backoff_seconds=backoff,
        retry_backoff_multiplier=1.0,
        max_total_seconds=0.0,  # disable per-invocation cap; test the external budget
        timeout=5.0,
    )
    rt = ClaudeCLIRuntime(cfg)
    rt._claude_path = "/usr/bin/claude"  # noqa: SLF001 - bypass shutil.which check
    rt.attach_budget(RuntimeBudget.starting_now(total_seconds=budget_seconds))
    return rt


class TestRetrySleepRespectsAttachedBudget:
    def test_retry_skipped_when_external_budget_cannot_cover_backoff(self) -> None:
        """If the planned sleep would push past the budget, skip it.

        We construct a budget that's already nearly exhausted (10ms left)
        and a backoff that exceeds it (100ms). The first attempt times
        out; the runtime should emit a timeout result without sleeping.
        """
        runtime = _runtime_with_budget(budget_seconds=0.01, retries=2, backoff=0.1)
        spy_sleep = _RecordingSleep()

        # Force every subprocess.run to time out so the retry path runs.
        import subprocess

        def fake_run(*_args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=kwargs.get("timeout", 1))

        with (
            patch("autocontext.runtimes.claude_cli._run_with_group_kill", side_effect=fake_run),
            patch("autocontext.runtimes.claude_cli.time.sleep", spy_sleep),
        ):
            output = runtime._invoke("hello", ["claude"])  # noqa: SLF001

        # The runtime must not have slept at all — the budget couldn't cover the backoff.
        assert spy_sleep.calls == [], f"unexpected sleeps: {spy_sleep.calls}"
        # And it should have returned a timeout-shaped result.
        assert output.metadata.get("error") in {"timeout", "runtime_budget_expired"}

    def test_retry_proceeds_when_external_budget_can_cover_backoff(self) -> None:
        """Sanity: when budget has room, the existing retry path still runs."""
        runtime = _runtime_with_budget(budget_seconds=120.0, retries=1, backoff=0.001)
        spy_sleep = _RecordingSleep()

        import subprocess

        attempts = {"count": 0}

        def fake_run(*_args, **kwargs):
            attempts["count"] += 1
            raise subprocess.TimeoutExpired(cmd="claude", timeout=kwargs.get("timeout", 1))

        with (
            patch("autocontext.runtimes.claude_cli._run_with_group_kill", side_effect=fake_run),
            patch("autocontext.runtimes.claude_cli.time.sleep", spy_sleep),
        ):
            runtime._invoke("hello", ["claude"])  # noqa: SLF001

        # Two subprocess invocations + one sleep between them.
        assert attempts["count"] == 2
        assert spy_sleep.calls == [0.001]


class TestNoBudgetUnchangedBehavior:
    """When no budget is attached, retry sleep behavior is unchanged."""

    def test_no_budget_still_sleeps_before_retry(self) -> None:
        cfg = ClaudeCLIConfig(
            max_retries=1,
            retry_backoff_seconds=0.002,
            retry_backoff_multiplier=1.0,
            max_total_seconds=0.0,
            timeout=5.0,
        )
        runtime = ClaudeCLIRuntime(cfg)
        runtime._claude_path = "/usr/bin/claude"  # noqa: SLF001
        # No attach_budget call — _budget is None.
        spy_sleep = _RecordingSleep()

        import subprocess

        attempts = {"count": 0}

        def fake_run(*_args, **kwargs):
            attempts["count"] += 1
            raise subprocess.TimeoutExpired(cmd="claude", timeout=kwargs.get("timeout", 1))

        with (
            patch("autocontext.runtimes.claude_cli._run_with_group_kill", side_effect=fake_run),
            patch("autocontext.runtimes.claude_cli.time.sleep", spy_sleep),
        ):
            runtime._invoke("hello", ["claude"])  # noqa: SLF001

        assert attempts["count"] == 2
        assert spy_sleep.calls == [0.002]
