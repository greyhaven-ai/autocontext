"""autocontext-native source: incremental reader over a loop runs database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from autocontext.ambient.sources.contract import RawTrace, SourcePoll


@dataclass(slots=True)
class NativeRunsSource:
    name: str
    runs_db_path: Path
    batch_size: int = 500

    def poll(self, cursor: str | None) -> SourcePoll:
        if not self.runs_db_path.exists():
            return SourcePoll()
        watermark = int(cursor or 0)
        conn = sqlite3.connect(self.runs_db_path)
        try:
            rows = conn.execute(
                "SELECT g.rowid, g.run_id, r.scenario, g.generation_index, g.mean_score, "
                "g.best_score, g.gate_decision, g.status, g.created_at "
                "FROM generations g JOIN runs r ON r.run_id = g.run_id "
                "WHERE g.rowid > ? ORDER BY g.rowid LIMIT ?",
                (watermark, self.batch_size),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return SourcePoll()
        records = [
            RawTrace(
                kind="generation",
                payload={
                    "run_id": row[1],
                    "scenario": row[2],
                    "generation_index": row[3],
                    "mean_score": row[4],
                    "best_score": row[5],
                    "gate_decision": row[6],
                    "status": row[7],
                    "created_at": row[8],
                },
            )
            for row in rows
        ]
        return SourcePoll(records=records, next_cursor=str(rows[-1][0]))
