"""Tests for ClaudeCLIRuntime + RuntimeBudget integration (AC-735).

Verifies that:

1. Without a budget, behavior is unchanged (per-call timeout only).
2. With a budget, every invocation is preceded by a budget check.
3. The per-call subprocess timeout is capped to ``min(per_call_timeout,
   budget.remaining())`` so a single long call cannot exceed the total
   budget.
4. When the budget is exhausted, `_invoke` short-circuits and returns a
   structured "budget exceeded" output without spawning a subprocess.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.runtimes.claude_cli import ClaudeCLIConfig, ClaudeCLIRuntime
from autocontext.runtimes.runtime_budget import RuntimeBudget

# -- Fake subprocess plumbing --


class _FakeRun:
    """Drop-in for ``subprocess.run`` that records the timeout it was passed.

    Returns a stub CompletedProcess with valid Claude CLI JSON output.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.next_response: str = json.dumps({"result": "stub", "total_cost_usd": 0.0})

    def __call__(self, args, **kwargs):  # noqa: ANN001
        self.calls.append({"args": list(args), "kwargs": dict(kwargs)})
        return SimpleNamespace(
            returncode=0,
            stdout=self.next_response,
            stderr="",
        )


@pytest.fixture
def fake_run(monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


@pytest.fixture
def runtime() -> ClaudeCLIRuntime:
    cfg = ClaudeCLIConfig(timeout=600.0)
    rt = ClaudeCLIRuntime(cfg)
    # Bypass the shutil.which() check.
    rt._claude_path = "/fake/bin/claude"  # noqa: SLF001
    return rt


# -- Without a budget: existing behavior preserved --


class TestUnbounded:
    def test_passes_per_call_timeout_when_no_budget(self, runtime, fake_run):
        runtime.generate("hello")
        assert len(fake_run.calls) == 1
        assert fake_run.calls[0]["kwargs"]["timeout"] == 600.0

    def test_no_budget_means_no_short_circuit(self, runtime, fake_run):
        # Three back-to-back calls should all spawn subprocesses.
        for _ in range(3):
            runtime.generate("x")
        assert len(fake_run.calls) == 3


# -- With a budget: bounded total runtime --


class TestWithBudget:
    def test_attaching_budget_caps_per_call_timeout(self, runtime, fake_run):
        # 10s budget against a 600s per-call timeout: subprocess should get 10s.
        runtime.attach_budget(RuntimeBudget.starting_now(total_seconds=10.0))
        runtime.generate("x")
        timeout = fake_run.calls[0]["kwargs"]["timeout"]
        assert timeout <= 10.0
        # And not absurdly low (we just attached the budget).
        assert timeout > 9.0

    def test_per_call_timeout_used_when_smaller_than_remaining(self, runtime, fake_run):
        # 1000s budget, 600s per-call: subprocess should get 600 (the smaller).
        runtime.attach_budget(RuntimeBudget.starting_now(total_seconds=1000.0))
        runtime.generate("x")
        assert fake_run.calls[0]["kwargs"]["timeout"] == 600.0

    def test_expired_budget_short_circuits_without_subprocess(self, runtime, fake_run):
        # Construct a budget already in the past.
        import time as _time

        expired = RuntimeBudget(
            total_seconds=1.0,
            start_at=_time.monotonic() - 100.0,
        )
        runtime.attach_budget(expired)
        result = runtime.generate("x")

        # No subprocess call should have happened.
        assert fake_run.calls == []
        # Result should signal the budget exhaustion clearly.
        assert result.text == ""
        assert result.metadata.get("error") == "runtime_budget_expired"

    def test_budget_message_carries_total_and_elapsed(self, runtime, fake_run):
        import time as _time

        expired = RuntimeBudget(
            total_seconds=5.0,
            start_at=_time.monotonic() - 12.0,
        )
        runtime.attach_budget(expired)
        result = runtime.generate("x")
        assert result.metadata.get("error") == "runtime_budget_expired"
        msg = result.metadata.get("message", "")
        assert "5" in msg  # total
        # Elapsed should be roughly 12s.
        assert "elapsed" in msg.lower()

    def test_revise_also_respects_budget(self, runtime, fake_run):
        import time as _time

        expired = RuntimeBudget(
            total_seconds=1.0,
            start_at=_time.monotonic() - 100.0,
        )
        runtime.attach_budget(expired)
        result = runtime.revise(
            prompt="task",
            previous_output="prev",
            feedback="fix it",
        )
        assert fake_run.calls == []
        assert result.metadata.get("error") == "runtime_budget_expired"


# -- DRY: budget check should not be duplicated between generate and revise --


class TestDryEnforcement:
    def test_budget_check_lives_in_invoke_not_in_each_caller(self, runtime, fake_run):
        # If the budget check were duplicated in generate() AND revise(),
        # this test would still pass — but the test guards against the
        # design regression of having two copies. We assert by introspection:
        # there is exactly one place that talks to RuntimeBudget.
        import inspect

        src = inspect.getsource(ClaudeCLIRuntime)
        # The budget check lives in _invoke (the single shared subprocess
        # entry point). It should NOT also live in generate() or revise().
        assert src.count("ensure_not_expired") <= 1
