"""the source contract: poll with a cursor, get records and the next cursor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


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
