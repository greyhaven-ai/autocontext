"""windowed gpu-hours usage ledger: makes the charter's budget floor enforceable.

Timestamps are supplied by the caller as ISO-8601 strings so the ledger is
deterministic and clock-free in tests. The train stage records the hours a
job actually consumed and reads used_in_window before the next job.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ambient_usage (
    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    gpu_hours REAL NOT NULL,
    at_iso TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ambient_usage_target ON ambient_usage(target);
"""


class UsageLedger:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def record(self, target: str, gpu_hours: float, at_iso: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO ambient_usage (target, gpu_hours, at_iso) VALUES (?, ?, ?)",
                (target, gpu_hours, at_iso),
            )
            return int(cursor.lastrowid or 0)

    def used_in_window(self, target: str, window_hours: int, now_iso: str) -> float:
        cutoff = (datetime.fromisoformat(now_iso) - timedelta(hours=window_hours)).isoformat()
        with self._connect() as conn:
            # at_iso > cutoff is lexicographic; correct only because the ambient daemon
            # always stamps UTC (datetime.now(UTC).isoformat()), so every timestamp shares
            # the same +00:00 offset and string order equals chronological order.
            # strict > cutoff: a record exactly window_hours old has aged out
            row = conn.execute(
                "SELECT COALESCE(SUM(gpu_hours), 0.0) FROM ambient_usage WHERE target = ? AND at_iso > ?",
                (target, cutoff),
            ).fetchone()
            return float(row[0])

    def used_in_window_all(self, window_hours: int, now_iso: str) -> float:
        # charter-wide pool: sum every target's hours in the window. record()
        # stays per-target for attribution, but the budget gate reads this
        # total so a charter with N targets cannot spend N times the window.
        cutoff = (datetime.fromisoformat(now_iso) - timedelta(hours=window_hours)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(gpu_hours), 0.0) FROM ambient_usage WHERE at_iso > ?",
                (cutoff,),
            ).fetchone()
            return float(row[0])

    def total(self, target: str) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(gpu_hours), 0.0) FROM ambient_usage WHERE target = ?",
                (target,),
            ).fetchone()
            return float(row[0])
