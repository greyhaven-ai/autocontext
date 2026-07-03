"""autocontext-native source: incremental reader over a loop runs database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from autocontext.ambient.sources.contract import RawTrace, SourcePoll

# terminal generation states. The loop upserts a "running" placeholder row at
# generation start (loop/generation_runner.py:1173; also cli.py:298 and
# knowledge/solve_task_execution.py:211) and completion is an in-place,
# rowid-preserving UPDATE that flips status to a terminal value: "completed"
# (loop/stages.py:1101, knowledge/package.py:268, cli.py:366,
# solve_task_execution.py:319) or "failed" (loop/generation_runner.py:992/1272/1301,
# cli.py:316, solve_task_execution.py:280). Only terminal rows carry final scores.
_TERMINAL_STATUSES = frozenset({"completed", "failed"})


@dataclass(slots=True)
class NativeRunsSource:
    """incremental reader over a loop runs database.

    Watermarks on the generations rowid. rowids are stable (the loop neither
    deletes generations nor VACUUMs; a manual VACUUM could renumber rowids and
    silently skip rows below the cursor), but a generation row mutates in place
    until terminal: it is upserted as a "running" placeholder at generation
    start and the same rowid is UPDATEd to a terminal status ("completed" or
    "failed") on completion. Because a rowid is read past the watermark exactly
    once, this source only consumes the contiguous terminal prefix of a batch:
    it stops at the first non-terminal row so an in-flight generation blocks the
    watermark (and is re-read with its final values on a later poll) rather than
    being captured forever as a placeholder. Known v1 tradeoff: a crashed,
    forever-"running" row holds the cursor until the loop's idempotent rerun
    marks it terminal.
    """

    name: str
    runs_db_path: Path
    kind: str = "autocontext"
    batch_size: int = 500

    def poll(self, cursor: str | None) -> SourcePoll:
        if not self.runs_db_path.exists():
            return SourcePoll()
        watermark = int(cursor or 0)
        # read-only open: least privilege for a pure reader, and it can
        # never take a write lock against the live loop
        conn = sqlite3.connect(f"{self.runs_db_path.resolve().as_uri()}?mode=ro", uri=True)
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
        # truncate at the first non-terminal (in-flight) row: only the
        # contiguous terminal prefix is safe to ingest and advance past, so an
        # in-flight row blocks the watermark instead of being skipped
        terminal_prefix = []
        for row in rows:
            if row[7] not in _TERMINAL_STATUSES:
                break
            terminal_prefix.append(row)
        if not terminal_prefix:
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
            for row in terminal_prefix
        ]
        return SourcePoll(records=records, next_cursor=str(terminal_prefix[-1][0]))
