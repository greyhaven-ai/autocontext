"""Tests for RuntimeBudget — wall-clock deadline value object (AC-735).

The domain concept: a sequence of subprocess invocations should have a
TOTAL wall-clock budget, beyond which no further invocation is allowed.
This is distinct from a per-call timeout: a sequence of 1 800-second calls
can run forever even if no single call exceeds 1 800 seconds.

Tests cover the value object contract: creation, remaining-time arithmetic,
expiry detection, and the domain exception raised when work would exceed
the budget.
"""

from __future__ import annotations

import time

import pytest

from autocontext.runtimes.runtime_budget import (
    RuntimeBudget,
    RuntimeBudgetExpired,
)

# -- Construction --


class TestStartingNow:
    def test_starts_with_full_budget_remaining(self):
        budget = RuntimeBudget.starting_now(total_seconds=120.0)
        # Allow a few microseconds of clock movement.
        assert budget.remaining() == pytest.approx(120.0, abs=0.05)

    def test_zero_budget_is_immediately_expired(self):
        budget = RuntimeBudget.starting_now(total_seconds=0.0)
        assert budget.expired() is True
        assert budget.remaining() == 0.0

    def test_negative_budget_is_rejected(self):
        # Negative budgets are nonsensical — explicit guard, not silent clamp.
        with pytest.raises(ValueError):
            RuntimeBudget.starting_now(total_seconds=-1.0)


class TestExplicitConstruction:
    def test_remaining_decreases_with_elapsed_time(self):
        # Construct with a known start time; pretend "now" is later.
        start = 1000.0
        budget = RuntimeBudget(total_seconds=60.0, start_at=start)
        assert budget.remaining(now=start) == 60.0
        assert budget.remaining(now=start + 30.0) == 30.0
        assert budget.remaining(now=start + 60.0) == 0.0

    def test_remaining_clamped_to_zero_after_deadline(self):
        # Past the deadline, remaining stays at 0 — never goes negative.
        start = 1000.0
        budget = RuntimeBudget(total_seconds=10.0, start_at=start)
        assert budget.remaining(now=start + 100.0) == 0.0
        assert budget.remaining(now=start + 1000.0) == 0.0


# -- Expiry --


class TestExpired:
    def test_not_expired_before_deadline(self):
        start = 1000.0
        budget = RuntimeBudget(total_seconds=10.0, start_at=start)
        assert budget.expired(now=start + 5.0) is False

    def test_expired_at_deadline(self):
        # The deadline itself counts as expired — no work allowed at t = deadline.
        start = 1000.0
        budget = RuntimeBudget(total_seconds=10.0, start_at=start)
        assert budget.expired(now=start + 10.0) is True

    def test_expired_after_deadline(self):
        start = 1000.0
        budget = RuntimeBudget(total_seconds=10.0, start_at=start)
        assert budget.expired(now=start + 11.0) is True


# -- Per-call timeout derivation --


class TestPerCallTimeout:
    def test_returns_min_of_requested_and_remaining(self):
        # If caller wants 60s and we have 100s left, allow 60s.
        # If caller wants 60s and we have 30s left, allow 30s.
        start = 1000.0
        budget = RuntimeBudget(total_seconds=100.0, start_at=start)
        assert budget.cap_call_timeout(60.0, now=start) == 60.0
        assert budget.cap_call_timeout(60.0, now=start + 70.0) == 30.0

    def test_cap_returns_zero_when_expired(self):
        start = 1000.0
        budget = RuntimeBudget(total_seconds=10.0, start_at=start)
        assert budget.cap_call_timeout(60.0, now=start + 20.0) == 0.0

    def test_cap_handles_unbounded_per_call(self):
        # If the per-call timeout is None, the budget alone bounds it.
        start = 1000.0
        budget = RuntimeBudget(total_seconds=100.0, start_at=start)
        assert budget.cap_call_timeout(None, now=start + 30.0) == 70.0


# -- Domain guard --


class TestEnsureNotExpired:
    def test_passes_silently_when_budget_remains(self):
        budget = RuntimeBudget.starting_now(total_seconds=10.0)
        # No exception expected.
        budget.ensure_not_expired()

    def test_raises_domain_exception_when_expired(self):
        start = 1000.0
        budget = RuntimeBudget(total_seconds=1.0, start_at=start)
        with pytest.raises(RuntimeBudgetExpired) as excinfo:
            budget.ensure_not_expired(now=start + 10.0)
        # Error message names the budget so operators can grep logs.
        assert "1.0" in str(excinfo.value) or "budget" in str(excinfo.value).lower()

    def test_exception_carries_total_and_elapsed(self):
        start = 1000.0
        budget = RuntimeBudget(total_seconds=5.0, start_at=start)
        with pytest.raises(RuntimeBudgetExpired) as excinfo:
            budget.ensure_not_expired(now=start + 12.5)
        exc = excinfo.value
        assert exc.total_seconds == 5.0
        assert exc.elapsed_seconds == pytest.approx(12.5)


# -- Immutability (value object discipline) --


class TestImmutable:
    def test_cannot_mutate_total_seconds(self):
        budget = RuntimeBudget.starting_now(total_seconds=10.0)
        with pytest.raises((AttributeError, TypeError)):
            budget.total_seconds = 999.0  # type: ignore[misc]

    def test_cannot_mutate_start_at(self):
        budget = RuntimeBudget.starting_now(total_seconds=10.0)
        with pytest.raises((AttributeError, TypeError)):
            budget.start_at = 0.0  # type: ignore[misc]


# -- Integration with monotonic clock --


class TestMonotonicClock:
    def test_remaining_uses_monotonic_clock_by_default(self):
        # Sleep briefly; remaining should decrease.
        budget = RuntimeBudget.starting_now(total_seconds=10.0)
        time.sleep(0.01)
        assert budget.remaining() < 10.0
        assert budget.remaining() > 9.5  # but not by much
