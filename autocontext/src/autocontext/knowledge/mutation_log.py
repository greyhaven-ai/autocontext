"""AC-235: Append-only context mutation log and replay from last-known-good state.

Provides an auditable, append-only JSONL log of context mutations per scenario,
with checkpoint support for replay from last-known-good state.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

MUTATION_TYPES = frozenset({
    "schema_change",
    "lesson_added",
    "lesson_removed",
    "playbook_updated",
    "notebook_updated",
    "run_outcome",
    "checkpoint",
})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class MutationEntry:
    """A single mutation event in the context log."""

    mutation_type: str
    generation: int
    payload: dict[str, Any]
    timestamp: str = ""
    run_id: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutation_type": self.mutation_type,
            "generation": self.generation,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MutationEntry:
        return cls(
            mutation_type=str(data.get("mutation_type", "")),
            generation=int(data.get("generation", 0)),
            payload=data.get("payload", {}),
            timestamp=str(data.get("timestamp", "")),
            run_id=str(data.get("run_id", "")),
            description=str(data.get("description", "")),
        )


@dataclass(slots=True)
class Checkpoint:
    """A known-good state marker in the mutation log."""

    generation: int
    run_id: str
    entry_index: int
    timestamp: str = ""


class MutationLog:
    """Append-only JSONL-backed mutation log per scenario."""

    def __init__(self, knowledge_root: Path, max_entries: int = 1000) -> None:
        self.knowledge_root = knowledge_root
        self.max_entries = max_entries

    def _log_path(self, scenario: str) -> Path:
        return self.knowledge_root / scenario / "mutation_log.jsonl"

    def append(self, scenario: str, entry: MutationEntry) -> None:
        path = self._log_path(scenario)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.to_dict()) + "\n")
        self.truncate(scenario)

    def read(
        self,
        scenario: str,
        *,
        mutation_types: list[str] | None = None,
        min_generation: int | None = None,
        max_generation: int | None = None,
    ) -> list[MutationEntry]:
        path = self._log_path(scenario)
        if not path.exists():
            return []
        entries: list[MutationEntry] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = MutationEntry.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
            if mutation_types and entry.mutation_type not in mutation_types:
                continue
            if min_generation is not None and entry.generation < min_generation:
                continue
            if max_generation is not None and entry.generation > max_generation:
                continue
            entries.append(entry)
        return entries

    def create_checkpoint(
        self, scenario: str, generation: int, run_id: str,
    ) -> Checkpoint:
        all_entries = self.read(scenario)
        entry_index = len(all_entries)  # index of the checkpoint entry about to be appended
        ts = _now_iso()
        self.append(
            scenario,
            MutationEntry(
                mutation_type="checkpoint",
                generation=generation,
                payload={"run_id": run_id, "entry_index": entry_index},
                timestamp=ts,
                run_id=run_id,
                description=f"Checkpoint at generation {generation}",
            ),
        )
        return Checkpoint(
            generation=generation,
            run_id=run_id,
            entry_index=entry_index,
            timestamp=ts,
        )

    def get_last_checkpoint(self, scenario: str) -> Checkpoint | None:
        all_entries = self.read(scenario)
        for idx in range(len(all_entries) - 1, -1, -1):
            entry = all_entries[idx]
            if entry.mutation_type == "checkpoint":
                return Checkpoint(
                    generation=entry.generation,
                    run_id=entry.run_id,
                    entry_index=idx,
                    timestamp=entry.timestamp,
                )
        return None

    def replay_after_checkpoint(
        self,
        scenario: str,
        *,
        mutation_types: list[str] | None = None,
    ) -> list[MutationEntry]:
        all_entries = self.read(scenario)
        if not all_entries:
            return []
        checkpoint = self.get_last_checkpoint(scenario)
        if checkpoint is None:
            start = 0
        else:
            start = checkpoint.entry_index + 1
        result = all_entries[start:]
        if mutation_types:
            result = [entry for entry in result if entry.mutation_type in mutation_types]
        return result

    def truncate(self, scenario: str) -> None:
        """Bound the log to max_entries, preserving the last checkpoint when possible."""
        all_entries = self.read(scenario)
        if len(all_entries) <= self.max_entries:
            return

        checkpoint = self.get_last_checkpoint(scenario)
        tail_start = len(all_entries) - self.max_entries
        if checkpoint is not None:
            # Preserve the checkpoint only if it fits within the retained tail.
            keep_from = checkpoint.entry_index if checkpoint.entry_index >= tail_start else tail_start
        else:
            keep_from = tail_start

        kept = all_entries[keep_from:]
        path = self._log_path(scenario)
        path.write_text(
            "".join(json.dumps(entry.to_dict()) + "\n" for entry in kept),
            encoding="utf-8",
        )

    def replay_summary(self, scenario: str, *, max_entries: int = 10) -> str:
        """Summarize recent mutations since the last checkpoint for prompt context."""
        replayed = self.replay_after_checkpoint(scenario)
        if not replayed:
            return ""

        lines = ["Context mutations since last checkpoint:"]
        for entry in replayed[-max_entries:]:
            detail = entry.description or entry.payload
            lines.append(
                f"- gen {entry.generation}: {entry.mutation_type} — {detail}"
            )
        return "\n".join(lines)

    def audit_summary(self, scenario: str) -> str:
        """Generate a human-readable audit summary."""
        all_entries = self.read(scenario)
        if not all_entries:
            return "No mutations recorded."
        type_counts: dict[str, int] = {}
        for entry in all_entries:
            type_counts[entry.mutation_type] = type_counts.get(entry.mutation_type, 0) + 1
        total = len(all_entries)
        lines = [f"## Mutation Log Audit ({total} total entries)"]
        for mtype, count in sorted(type_counts.items()):
            lines.append(f"- {mtype}: {count}")
        checkpoint = self.get_last_checkpoint(scenario)
        if checkpoint:
            lines.append(f"- Last checkpoint: generation {checkpoint.generation} (run {checkpoint.run_id})")
        return "\n".join(lines)
