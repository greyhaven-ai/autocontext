"""the curate stage: continuous per-target dataset construction with guardrails."""

from __future__ import annotations

from dataclasses import dataclass

from autocontext.ambient.charter import CharterTarget
from autocontext.ambient.datasets import DatasetStore
from autocontext.ambient.eligibility import assess, is_evaluative_target, to_training_record
from autocontext.ambient.stage import StageContext, StageResult
from autocontext.ambient.trace_store import TraceStore


@dataclass(slots=True)
class CurateStage:
    name: str
    trace_store: TraceStore
    dataset_store: DatasetStore
    batch_size: int = 500

    def run_once(self, ctx: StageContext) -> StageResult:
        processed = 0
        errors = 0
        for target in ctx.charter.targets:
            if is_evaluative_target(target):
                # asymmetric trainability: v1 has no externally anchored
                # labels, so evaluative-role datasets are refused outright
                ctx.emitter.emit(
                    "curate_target_skipped",
                    {"target": target.name, "reason": "evaluative_role"},
                    channel="ambient",
                )
                continue
            try:
                processed += self._curate_target(ctx, target)
            except Exception as exc:
                errors += 1
                ctx.emitter.emit(
                    "curate_target_failed",
                    {"target": target.name, "error": str(exc)},
                    channel="ambient",
                )
        return StageResult(processed=processed, errors=errors)

    def _curate_target(self, ctx: StageContext, target: CharterTarget) -> int:
        manifest = self.dataset_store.load_manifest(target.name)
        traces = self.trace_store.read_after(manifest.last_record_id, self.batch_size, kind="agent_output")
        if not traces:
            return 0
        records = []
        scores: list[float] = []
        quarantined = 0
        skipped = 0
        for trace in traces:
            decision = assess(trace, target)
            if decision.eligible:
                record = to_training_record(trace)
                records.append(record)
                scores.append(float(record["score"]))
            elif decision.reason == "quarantined_provenance":
                quarantined += 1
            else:
                skipped += 1
        appended = self.dataset_store.append_records(target.name, records)
        # the cursor advances past ineligible traces too; they were assessed
        # and must not be re-assessed every cycle. KNOWN v1 lose-data window:
        # read_after silently skips ids it can no longer find, so if ingest's
        # disk-quota prune deletes the oldest traces (it ignores per-target
        # cursors) before this cursor reaches them, those un-curated traces are
        # lost, not duplicated; a later slice can floor that prune at the minimum
        # manifest cursor. Crash ordering note: the
        # dataset append lands before the manifest (cursor) save, so a crash
        # between them can duplicate this batch's records in the jsonl on the
        # next cycle; dedupe at train time (data_selection.dedupe_records)
        # absorbs that, and the manifest itself never goes torn (atomic replace).
        updated = self.dataset_store.absorb(
            manifest,
            appended_scores=scores,
            quarantined=quarantined,
            skipped=skipped,
            last_record_id=traces[-1].record_id,
        )
        self.dataset_store.save_manifest(updated)
        ctx.emitter.emit(
            "curate_target_updated",
            {
                "target": target.name,
                "appended": appended,
                "quarantined": quarantined,
                "skipped": skipped,
                "record_count": updated.record_count,
            },
            channel="ambient",
        )
        return appended
