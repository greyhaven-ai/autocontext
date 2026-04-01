"""Tests for adaptive context-pressure management (AC-508).

DDD bounded context: ContextPressure measures window utilization,
CompactionPolicy drives staged compaction decisions,
CompactionPipeline executes cheapest-first recovery.
"""

from __future__ import annotations

import pytest


class TestContextPressure:
    """Pressure value object measures context window utilization."""

    def test_healthy_pressure(self) -> None:
        from autocontext.session.context_pressure import ContextPressure, PressureLevel

        pressure = ContextPressure.measure(
            used_tokens=10_000,
            effective_window=100_000,
        )
        assert pressure.level == PressureLevel.HEALTHY
        assert pressure.utilization == pytest.approx(0.1)
        assert not pressure.should_compact

    def test_warning_pressure(self) -> None:
        from autocontext.session.context_pressure import ContextPressure, PressureLevel

        pressure = ContextPressure.measure(
            used_tokens=75_000,
            effective_window=100_000,
        )
        assert pressure.level == PressureLevel.WARNING
        assert not pressure.should_compact  # warning, not yet compacting

    def test_compact_soon_pressure(self) -> None:
        from autocontext.session.context_pressure import ContextPressure, PressureLevel

        pressure = ContextPressure.measure(
            used_tokens=88_000,
            effective_window=100_000,
        )
        assert pressure.level == PressureLevel.COMPACT_SOON
        assert pressure.should_compact

    def test_blocking_pressure(self) -> None:
        from autocontext.session.context_pressure import ContextPressure, PressureLevel

        pressure = ContextPressure.measure(
            used_tokens=97_000,
            effective_window=100_000,
        )
        assert pressure.level == PressureLevel.BLOCKING
        assert pressure.should_compact

    def test_custom_thresholds(self) -> None:
        from autocontext.session.context_pressure import (
            CompactionPolicy,
            ContextPressure,
            PressureLevel,
        )

        policy = CompactionPolicy(
            warning_threshold=0.5,
            compact_threshold=0.7,
            blocking_threshold=0.9,
        )
        pressure = ContextPressure.measure(
            used_tokens=60_000,
            effective_window=100_000,
            policy=policy,
        )
        assert pressure.level == PressureLevel.WARNING

    def test_utilization_snapshot_stays_consistent_with_threshold_level(self) -> None:
        from autocontext.session.context_pressure import (
            CompactionPolicy,
            ContextPressure,
            PressureLevel,
        )

        policy = CompactionPolicy()
        pressure = ContextPressure.measure(
            used_tokens=84_996,
            effective_window=100_000,
            policy=policy,
        )

        assert pressure.level == PressureLevel.WARNING
        assert pressure.utilization == pytest.approx(0.84996)
        assert pressure.utilization < policy.compact_threshold


class TestEffectiveWindow:
    """Effective window = raw window - output headroom - overhead."""

    def test_effective_window_reserves_headroom(self) -> None:
        from autocontext.session.context_pressure import effective_window

        raw = 128_000
        eff = effective_window(raw, output_headroom=4_096, overhead=1_000)
        assert eff == 128_000 - 4_096 - 1_000

    def test_effective_window_minimum_floor(self) -> None:
        from autocontext.session.context_pressure import effective_window

        # Even with huge headroom, floor is > 0
        eff = effective_window(1_000, output_headroom=900, overhead=200)
        assert eff > 0


class TestCompactionPolicy:
    """Policy configures what to preserve, compress, and discard."""

    def test_default_policy(self) -> None:
        from autocontext.session.context_pressure import CompactionPolicy

        policy = CompactionPolicy()
        assert policy.warning_threshold < policy.compact_threshold
        assert policy.compact_threshold < policy.blocking_threshold
        assert len(policy.protected_classes) > 0  # at least some things are protected

    def test_context_class_categories(self) -> None:
        from autocontext.session.context_pressure import CompactionPolicy

        policy = CompactionPolicy()
        # Goal/plan/blockers should be protected
        assert "goal" in policy.protected_classes
        # Stale narrative should be compressible
        assert "narrative_history" in policy.compressible_classes

    def test_invalid_threshold_order_rejected(self) -> None:
        from autocontext.session.context_pressure import CompactionPolicy

        with pytest.raises(ValueError, match="warning_threshold < compact_threshold"):
            CompactionPolicy(
                warning_threshold=0.9,
                compact_threshold=0.7,
                blocking_threshold=0.8,
            )


class TestCompactionResult:
    """Compaction produces a structured, auditable result."""

    def test_compaction_result_tracks_savings(self) -> None:
        from autocontext.session.context_pressure import CompactionResult

        result = CompactionResult(
            stage="micro",
            tokens_before=80_000,
            tokens_after=60_000,
            preserved=["goal", "plan", "latest_tool_output"],
            discarded=["stale_narrative_0", "stale_narrative_1"],
            safe_to_continue=True,
        )
        assert result.tokens_freed == 20_000
        assert result.safe_to_continue

    def test_failed_compaction(self) -> None:
        from autocontext.session.context_pressure import CompactionResult

        result = CompactionResult(
            stage="micro",
            tokens_before=80_000,
            tokens_after=79_000,
            preserved=[],
            discarded=[],
            safe_to_continue=False,
            error="insufficient_savings",
        )
        assert result.tokens_freed == 1_000
        assert not result.safe_to_continue


class TestCircuitBreaker:
    """Stops repeated compaction loops from running indefinitely."""

    def test_circuit_breaker_trips_after_max_failures(self) -> None:
        from autocontext.session.context_pressure import CompactionCircuitBreaker

        breaker = CompactionCircuitBreaker(max_failures=3)
        assert not breaker.is_open

        breaker.record_failure("stage_1")
        breaker.record_failure("stage_2")
        assert not breaker.is_open

        breaker.record_failure("stage_3")
        assert breaker.is_open

    def test_circuit_breaker_resets_on_success(self) -> None:
        from autocontext.session.context_pressure import CompactionCircuitBreaker

        breaker = CompactionCircuitBreaker(max_failures=2)
        breaker.record_failure("stage_1")
        breaker.record_success()
        breaker.record_failure("stage_2")
        assert not breaker.is_open  # reset after success
