"""agent-outputs source: incremental reader over a loop runs database's full output text."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from autocontext.ambient.sources.contract import TERMINAL_GENERATION_STATUSES, RawTrace, SourcePoll


@dataclass(slots=True)
class AgentOutputsSource:
    """incremental reader over agent_outputs joined to its parent generation.

    Watermarks on agent_outputs.id (AUTOINCREMENT, append-only). Output rows
    are inserted while their parent generation is still "running" and only the
    parent row's terminal UPDATE makes the labels final, so this source
    consumes only the contiguous prefix whose parent generation is terminal:
    it stops at the first row with an in-flight parent and re-reads it, with
    final labels, on a later poll. Known v1 tradeoff (same as
    NativeRunsSource): one crashed, forever-running generation holds the
    cursor for every later row until the loop's idempotent rerun marks it
    terminal.
    """

    name: str
    runs_db_path: Path
    kind: str = "autocontext-outputs"
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
                "SELECT ao.id, ao.run_id, r.scenario, ao.generation_index, ao.role, ao.content, "
                "ao.created_at, g.status, g.mean_score, g.best_score, g.gate_decision "
                "FROM agent_outputs ao "
                "JOIN generations g ON g.run_id = ao.run_id AND g.generation_index = ao.generation_index "
                "JOIN runs r ON r.run_id = ao.run_id "
                "WHERE ao.id > ? ORDER BY ao.id LIMIT ?",
                (watermark, self.batch_size),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            # A runs database without an agent_outputs table (hand-built
            # fixtures, manually assembled dbs) is a legitimate layout, not an
            # error: there is simply nothing for this source to read, so poll
            # empty and never trip the ingest breaker. The match is pinned to
            # this table: a db missing generations or runs is corruption and,
            # like any other operational failure (locked db), must propagate.
            if "no such table: agent_outputs" in str(exc):
                return SourcePoll()
            raise
        finally:
            conn.close()
        if not rows:
            return SourcePoll()
        terminal_prefix = []
        for row in rows:
            if row[7] not in TERMINAL_GENERATION_STATUSES:
                break
            terminal_prefix.append(row)
        if not terminal_prefix:
            return SourcePoll()
        records = [
            RawTrace(
                kind="agent_output",
                payload={
                    "run_id": row[1],
                    "scenario": row[2],
                    "generation_index": row[3],
                    "role": row[4],
                    "content": row[5],
                    "created_at": row[6],
                    "status": row[7],
                    "mean_score": row[8],
                    "best_score": row[9],
                    "gate_decision": row[10],
                },
            )
            for row in terminal_prefix
        ]
        return SourcePoll(records=records, next_cursor=str(terminal_prefix[-1][0]))
