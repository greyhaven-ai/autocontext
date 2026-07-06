"""tests for the windowed gpu-hours usage ledger."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from autocontext.ambient.usage import UsageLedger


def _iso(base: datetime, **delta: float) -> str:
    return (base + timedelta(**delta)).isoformat()


def test_used_in_window_sums_only_recent_records(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    now = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    ledger.record("prover", 2.0, _iso(now, hours=-30))  # outside a 24h window
    ledger.record("prover", 1.5, _iso(now, hours=-2))  # inside
    ledger.record("prover", 0.5, _iso(now, hours=-1))  # inside

    assert ledger.used_in_window("prover", window_hours=24, now_iso=now.isoformat()) == 2.0


def test_used_in_window_is_per_target(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    now = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    ledger.record("prover", 3.0, _iso(now, hours=-1))
    ledger.record("solver", 5.0, _iso(now, hours=-1))

    assert ledger.used_in_window("prover", 24, now.isoformat()) == 3.0
    assert ledger.used_in_window("solver", 24, now.isoformat()) == 5.0


def test_used_in_window_all_sums_across_targets(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    now = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    ledger.record("prover", 2.0, _iso(now, hours=-30))  # outside a 24h window
    ledger.record("prover", 1.5, _iso(now, hours=-2))  # inside
    ledger.record("solver", 0.5, _iso(now, hours=-1))  # inside, a different target

    # the charter-wide pool sums every target's in-window hours into one total
    assert ledger.used_in_window_all(window_hours=24, now_iso=now.isoformat()) == 2.0


def test_window_boundary_is_strict_greater_than(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    now = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    ledger.record("prover", 4.0, _iso(now, hours=-24))  # exactly on the boundary: excluded

    assert ledger.used_in_window("prover", 24, now.isoformat()) == 0.0


def test_total_sums_all_time(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    now = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    ledger.record("prover", 2.0, _iso(now, hours=-100))
    ledger.record("prover", 3.0, _iso(now, hours=-1))

    assert ledger.total("prover") == 5.0


def test_empty_ledger_reports_zero(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    assert ledger.used_in_window("prover", 24, "2026-07-06T12:00:00+00:00") == 0.0
    assert ledger.total("prover") == 0.0
