"""Adaptive context-pressure management (AC-508).

Domain concepts:
- ContextPressure: value object measuring window utilization
- PressureLevel: healthy → warning → compact-soon → blocking
- CompactionPolicy: thresholds + preservation/discard rules
- CompactionResult: structured outcome of a compaction attempt
- CompactionCircuitBreaker: stops runaway compaction loops

The pressure model measures from the *effective* window (raw window
minus output headroom and runtime overhead), not the advertised limit.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

# ---- Value Objects ----


class PressureLevel(StrEnum):
    """Context pressure states, measured from effective window edge."""

    HEALTHY = "healthy"
    WARNING = "warning"
    COMPACT_SOON = "compact_soon"
    BLOCKING = "blocking"


class CompactionPolicy(BaseModel):
    """Configures pressure thresholds and content classification.

    Thresholds are fractions of effective window utilization:
    - warning_threshold: operator-visible warning
    - compact_threshold: automatic compaction trigger
    - blocking_threshold: hard block until compaction succeeds
    """

    warning_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    compact_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    blocking_threshold: float = Field(default=0.95, ge=0.0, le=1.0)

    # Content classes for preservation decisions
    protected_classes: frozenset[str] = frozenset({
        "goal", "plan", "blockers", "verifier_findings",
        "latest_tool_output", "notebook_entries",
    })
    compressible_classes: frozenset[str] = frozenset({
        "narrative_history", "prior_summaries", "stale_progress_reports",
        "superseded_reasoning", "raw_tool_payloads",
    })
    discardable_classes: frozenset[str] = frozenset({
        "duplicate_summaries", "expired_debug_traces",
    })

    # Circuit breaker
    max_compaction_failures: int = 3

    # Eligibility guards
    min_compressible_tokens: int = 2_000
    min_meaningful_turns: int = 3
    max_preserved_tokens: int = 50_000

    @model_validator(mode="after")
    def _validate_threshold_order(self) -> CompactionPolicy:
        if not (
            self.warning_threshold < self.compact_threshold < self.blocking_threshold
        ):
            msg = (
                "Compaction thresholds must satisfy "
                "warning_threshold < compact_threshold < blocking_threshold"
            )
            raise ValueError(msg)
        return self

    model_config = {"frozen": True}


class ContextPressure(BaseModel):
    """Immutable snapshot of current context pressure."""

    used_tokens: int
    effective_window: int
    utilization: float
    level: PressureLevel

    @property
    def should_compact(self) -> bool:
        return self.level in (PressureLevel.COMPACT_SOON, PressureLevel.BLOCKING)

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.effective_window - self.used_tokens)

    @classmethod
    def measure(
        cls,
        used_tokens: int,
        effective_window: int,
        policy: CompactionPolicy | None = None,
    ) -> ContextPressure:
        """Measure current pressure against thresholds."""
        p = policy or CompactionPolicy()
        util = used_tokens / max(effective_window, 1)

        if util >= p.blocking_threshold:
            level = PressureLevel.BLOCKING
        elif util >= p.compact_threshold:
            level = PressureLevel.COMPACT_SOON
        elif util >= p.warning_threshold:
            level = PressureLevel.WARNING
        else:
            level = PressureLevel.HEALTHY

        return cls(
            used_tokens=used_tokens,
            effective_window=effective_window,
            utilization=util,
            level=level,
        )

    model_config = {"frozen": True}


class CompactionResult(BaseModel):
    """Structured outcome of a compaction attempt."""

    stage: str  # "micro", "session_memory", "full_fallback"
    tokens_before: int
    tokens_after: int
    preserved: list[str] = Field(default_factory=list)
    discarded: list[str] = Field(default_factory=list)
    safe_to_continue: bool = True
    error: str = ""

    @property
    def tokens_freed(self) -> int:
        return self.tokens_before - self.tokens_after

    model_config = {"frozen": True}


# ---- Functions ----


def effective_window(
    raw_window: int,
    output_headroom: int = 4_096,
    overhead: int = 512,
) -> int:
    """Compute effective context window after reserving headroom.

    Always returns at least 1 to avoid division by zero.
    """
    return max(1, raw_window - output_headroom - overhead)


# ---- Circuit Breaker ----


class CompactionCircuitBreaker:
    """Stops repeated compaction loops from running indefinitely.

    Opens after ``max_failures`` consecutive failures. Resets on success.
    """

    def __init__(self, max_failures: int = 3) -> None:
        self._max_failures = max_failures
        self._consecutive_failures = 0
        self._failure_log: list[str] = []

    @property
    def is_open(self) -> bool:
        return self._consecutive_failures >= self._max_failures

    def record_failure(self, stage: str) -> None:
        self._consecutive_failures += 1
        self._failure_log.append(stage)

    def record_success(self) -> None:
        self._consecutive_failures = 0

    @property
    def failure_log(self) -> list[str]:
        return list(self._failure_log)
