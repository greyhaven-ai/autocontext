"""RuntimeBudget — wall-clock deadline for a sequence of subprocess invocations.

AC-735: per-call subprocess timeouts (e.g. `subprocess.run(..., timeout=...)`)
do not bound the *total* wall-clock cost of a sequence of calls. A long-running
runtime can spawn many in-budget subprocess calls and still vastly exceed the
operator's intended ceiling.

This module introduces the domain concept of a runtime budget — an absolute
deadline measured from a fixed start time, enforced between subprocess calls
and used to cap each call's per-call timeout to no more than the remaining
budget. The budget is a frozen value object, immutable for its lifetime.

Usage shape::

    budget = RuntimeBudget.starting_now(total_seconds=28800.0)
    for prompt in prompts:
        budget.ensure_not_expired()
        # Cap the per-call subprocess timeout to remaining budget:
        timeout = budget.cap_call_timeout(per_call_timeout)
        run_subprocess(prompt, timeout=timeout)
"""

from __future__ import annotations

import time
from dataclasses import dataclass


class RuntimeBudgetExpired(Exception):
    """Domain exception: work attempted past the runtime budget deadline.

    Carries the configured total budget and the elapsed time so operators
    can reason about what happened from a single log line.
    """

    def __init__(self, total_seconds: float, elapsed_seconds: float) -> None:
        self.total_seconds = total_seconds
        self.elapsed_seconds = elapsed_seconds
        super().__init__(f"runtime budget expired: elapsed {elapsed_seconds:.1f}s of {total_seconds:.1f}s budget")


@dataclass(frozen=True, slots=True)
class RuntimeBudget:
    """An absolute wall-clock deadline beyond which no further work is allowed.

    Frozen value object. ``total_seconds`` is the configured ceiling;
    ``start_at`` is a monotonic-clock value (seconds since an arbitrary
    epoch, suitable for ``time.monotonic()``-style arithmetic).

    Use :meth:`starting_now` for the common case of "start counting now".
    Use the explicit constructor only when you need to pin ``start_at``
    to a specific monotonic value (tests; resuming an existing budget).
    """

    total_seconds: float
    start_at: float

    def __post_init__(self) -> None:
        if self.total_seconds < 0:
            raise ValueError(f"RuntimeBudget.total_seconds must be >= 0, got {self.total_seconds}")

    @classmethod
    def starting_now(cls, total_seconds: float) -> RuntimeBudget:
        """Construct a budget that begins counting from the current moment."""
        return cls(total_seconds=total_seconds, start_at=time.monotonic())

    # -- Decision predicates --

    def remaining(self, now: float | None = None) -> float:
        """Return seconds left until expiry, clamped to >= 0.0.

        ``now`` defaults to ``time.monotonic()``; tests pass an explicit
        value to avoid sleeping.
        """
        elapsed = (now if now is not None else time.monotonic()) - self.start_at
        remaining = self.total_seconds - elapsed
        return remaining if remaining > 0.0 else 0.0

    def expired(self, now: float | None = None) -> bool:
        """Return True iff no time remains in the budget.

        The deadline itself counts as expired (closed-open interval): no
        work is permitted at exactly the deadline moment.
        """
        return self.remaining(now=now) <= 0.0

    # -- Per-call timeout derivation --

    def cap_call_timeout(self, requested: float | None, now: float | None = None) -> float:
        """Return the smaller of ``requested`` and the remaining budget.

        ``requested=None`` means the caller has no per-call timeout; the
        budget alone bounds the call. The return value is suitable for
        passing as ``subprocess.run(timeout=...)``.
        """
        rem = self.remaining(now=now)
        if requested is None:
            return rem
        return rem if rem < requested else requested

    # -- Domain guard --

    def ensure_not_expired(self, now: float | None = None) -> None:
        """Raise :class:`RuntimeBudgetExpired` if the deadline has passed.

        Called by runtimes between subprocess invocations to bail before
        starting work that the budget could not accommodate even in the
        best case.
        """
        elapsed = (now if now is not None else time.monotonic()) - self.start_at
        if elapsed >= self.total_seconds:
            raise RuntimeBudgetExpired(
                total_seconds=self.total_seconds,
                elapsed_seconds=elapsed,
            )
