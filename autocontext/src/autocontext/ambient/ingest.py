"""the ingest stage: poll charter sources, redact, append to the trace store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autocontext.ambient.redaction_gate import redact_payload
from autocontext.ambient.sources.contract import TraceSource
from autocontext.ambient.stage import StageContext, StageResult
from autocontext.ambient.trace_store import TraceStore

_PRUNE_FRACTION = 0.1
# (kind, payload, produced_by, redaction_findings) as consumed by append_batch
_BatchRecord = tuple[str, dict[str, Any], str, int]


@dataclass(slots=True)
class IngestStage:
    name: str
    trace_store: TraceStore
    sources: list[TraceSource]
    disk_quota_gb: float
    unsupported: list[tuple[str, str]] = field(default_factory=list)
    _announced: bool = False

    def run_once(self, ctx: StageContext) -> StageResult:
        if not self._announced:
            # announced at run time, not construction, so read-only commands
            # (status builds the stage set too) never write events
            for source_name, kind in self.unsupported:
                ctx.emitter.emit("ingest_source_unsupported", {"source": source_name, "kind": kind}, channel="ambient")
            self._announced = True
        appended = 0
        errors = 0
        for source in self.sources:
            # kind-namespacing means a charter kind change restarts that
            # source's cursor cleanly instead of cross-reading a foreign format
            cursor_key = f"{source.kind}:{source.name}"
            cursor = self.trace_store.get_cursor(cursor_key)
            try:
                poll = source.poll(cursor)
                batch: list[_BatchRecord] = []
                for record in poll.records:
                    payload, findings = redact_payload(record.payload)
                    batch.append((record.kind, payload, record.produced_by, findings))
                # store-side failures are now atomic per batch (nothing
                # persisted, cursor unchanged), so a replay would need the
                # process to die between the source poll and this call, which
                # persists nothing; duplicates thus require a repeated poll of
                # the same source data, which the cursors prevent
                appended += self.trace_store.append_batch(cursor_key, batch, poll.next_cursor)
            except Exception as exc:
                errors += 1
                ctx.emitter.emit("ingest_source_failed", {"source": source.name, "error": str(exc)}, channel="ambient")
        if self.trace_store.db_size_bytes() > self.disk_quota_gb * 1024**3:
            deleted = self.trace_store.prune_oldest(_PRUNE_FRACTION)
            ctx.emitter.emit("trace_retention_pruned", {"deleted": deleted}, channel="ambient")
        ctx.emitter.emit("ingest_completed", {"appended": appended, "sources": len(self.sources)}, channel="ambient")
        return StageResult(processed=appended, errors=errors)
