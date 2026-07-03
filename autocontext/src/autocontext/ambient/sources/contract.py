"""the source contract: poll with a cursor, get records and the next cursor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# terminal generation states, shared by every reader of a loop runs database.
# The loop upserts a "running" placeholder row at generation start
# (loop/generation_runner.py:1173; also cli.py:298 and
# knowledge/solve_task_execution.py:211) and completion is an in-place,
# rowid-preserving UPDATE to "completed" (loop/stages.py:1101,
# knowledge/package.py:268, cli.py:366, solve_task_execution.py:319) or
# "failed" (loop/generation_runner.py:992/1272/1301, cli.py:316,
# solve_task_execution.py:280). Only terminal rows carry final values.
TERMINAL_GENERATION_STATUSES = frozenset({"completed", "failed"})


@dataclass(slots=True)
class RawTrace:
    kind: str
    payload: dict[str, Any]
    produced_by: str = "frontier"


@dataclass(slots=True)
class SourcePoll:
    records: list[RawTrace] = field(default_factory=list)
    next_cursor: str | None = None


class TraceSource(Protocol):
    name: str
    kind: str

    def poll(self, cursor: str | None) -> SourcePoll:
        """A poll that returns records must also return a next_cursor;
        returning records with next_cursor None would re-ingest the same
        batch every poll.
        """
        ...
