"""the ingest stage: poll charter sources, redact, append to the trace store."""

from __future__ import annotations

from dataclasses import dataclass

from autocontext.ambient.redaction_gate import redact_payload
from autocontext.ambient.sources.contract import TraceSource
from autocontext.ambient.stage import StageContext, StageResult
from autocontext.ambient.trace_store import TraceStore

_PRUNE_FRACTION = 0.1


@dataclass(slots=True)
class IngestStage:
    name: str
    trace_store: TraceStore
    sources: list[TraceSource]
    disk_quota_gb: float

    def run_once(self, ctx: StageContext) -> StageResult:
        appended = 0
        errors = 0
        for source in self.sources:
            cursor = self.trace_store.get_cursor(source.name)
            try:
                poll = source.poll(cursor)
                for record in poll.records:
                    payload, findings = redact_payload(record.payload)
                    self.trace_store.append(source.name, record.kind, payload, record.produced_by, findings)
                    appended += 1
                if poll.next_cursor is not None:
                    self.trace_store.set_cursor(source.name, poll.next_cursor)
            except Exception as exc:
                errors += 1
                ctx.emitter.emit("ingest_source_failed", {"source": source.name, "error": str(exc)}, channel="ambient")
        if self.trace_store.db_size_bytes() > self.disk_quota_gb * 1024**3:
            deleted = self.trace_store.prune_oldest(_PRUNE_FRACTION)
            ctx.emitter.emit("trace_retention_pruned", {"deleted": deleted}, channel="ambient")
        ctx.emitter.emit("ingest_completed", {"appended": appended, "sources": len(self.sources)}, channel="ambient")
        return StageResult(processed=appended, errors=errors)
