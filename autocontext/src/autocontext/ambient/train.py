"""the train stage: turn eligible target datasets into registry candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from autocontext.ambient.charter import CharterTarget
from autocontext.ambient.datasets import DatasetStore
from autocontext.ambient.policy import budget_allows, decide
from autocontext.ambient.publish import publish_candidate
from autocontext.ambient.stage import StageContext, StageResult
from autocontext.ambient.training_backend import TrainRequest, is_deadline_capable, run_training, select_backend
from autocontext.ambient.usage import UsageLedger
from autocontext.training.model_registry import ModelRegistry


def _default_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class TrainStage:
    name: str
    dataset_store: DatasetStore
    usage_ledger: UsageLedger
    registry: ModelRegistry
    artifacts_root: Path
    checkpoints_root: Path
    now_fn: Callable[[], str] = _default_now
    time_budget_seconds: int = 1800
    memory_limit_mb: int = 8192
    assess_overhead_seconds: int = 1800

    def run_once(self, ctx: StageContext) -> StageResult:
        processed = 0
        errors = 0
        for target in ctx.charter.targets:
            try:
                processed += self._train_target(ctx, target)
            except Exception as exc:
                errors += 1
                ctx.emitter.emit("train_target_failed", {"target": target.name, "error": str(exc)}, channel="ambient")
        return StageResult(processed=processed, errors=errors)

    def _train_target(self, ctx: StageContext, target: CharterTarget) -> int:
        manifest = self.dataset_store.load_manifest(target.name)
        if manifest.record_count < target.min_dataset_records:
            return 0  # not enough data yet: quiet skip
        if self._already_trained(target.name, manifest.record_count):
            # the dataset has not grown since the last candidate for this target: retraining the
            # same data would recompute an identical (idempotent) registry record while charging gpu
            # hours again. skip before any budget event or training so the no-op cycle costs nothing.
            ctx.emitter.emit(
                "train_up_to_date",
                {"target": target.name, "record_count": manifest.record_count},
                channel="ambient",
            )
            return 0
        decision = decide(ctx.charter, "train", target.name)
        if decision.requires_approval:
            ctx.emitter.emit("train_requires_approval", {"target": target.name, "reason": decision.reason}, channel="ambient")
            return 0
        backend = select_backend(target.method)
        if backend is None:
            # no installed backend supports this method (a frontier-only ci box):
            # report and skip, never an error, so the stage breaker does not trip; a box that
            # cannot train at all should not spend a budget event to discover that
            ctx.emitter.emit("train_no_backend", {"target": target.name, "method": target.method}, channel="ambient")
            return 0
        # pre-flight budget: gate on the estimate BEFORE training so a breaching job never spends
        # the hours; the record step below then charges the ACTUAL hours consumed. the reservation
        # is asymmetric by backend: a deadline-capable backend (sft) enforces a real wall-clock
        # deadline (run_sft_training's DeadlineCallback stops the run at time_budget), so its
        # TRAINING COMPUTE cannot exceed the ceiling and we reserve time_budget exactly. that is what
        # the recorded gpu_hours measures (training_seconds), so recorded usage stays within the
        # reservation. note the deadline bounds compute only, not model download/load: a first run
        # pulling a large base model does that load OUTSIDE the deadline and it is not charged to the
        # ledger, so total box-occupancy for such a run can exceed the window even though recorded
        # usage does not. mlxlm has no in-run deadline: its timeout bounds only the training
        # subprocess, so adapter load + assessment run afterward in-process and are counted in
        # training_seconds too, so it keeps a conservative envelope covering that assess overhead.
        if is_deadline_capable(backend):
            requested_gpu_hours = self.time_budget_seconds / 3600.0
        else:
            requested_gpu_hours = (self.time_budget_seconds + self.assess_overhead_seconds) / 3600.0
        # charter-wide pool: read every target's in-window hours, not just this target's, so a
        # charter with N targets cannot each spend a full window. record() below stays per-target
        # for attribution; only the gate reads the shared total.
        used = self.usage_ledger.used_in_window_all(ctx.charter.budgets.window_hours, self.now_fn())
        if not budget_allows(ctx.charter.budgets, used, requested_gpu_hours):
            ctx.emitter.emit(
                "train_budget_exhausted",
                {
                    "target": target.name,
                    "used_gpu_hours": used,
                    "requested_gpu_hours": requested_gpu_hours,
                    "window_hours": ctx.charter.budgets.window_hours,
                },
                channel="ambient",
            )
            return 0
        scenario = self._scenario_for(target)
        request = TrainRequest(
            scenario=scenario,
            data_path=self.dataset_store.dataset_path(target.name),
            # per-record_count checkpoint dir: artifact identity varies with dataset size, so a
            # later (larger) count must not overwrite an earlier count's on-disk adapter.
            output_dir=self.checkpoints_root / target.name / str(manifest.record_count),
            base_model=target.base_model,
            time_budget_seconds=self.time_budget_seconds,
            memory_limit_mb=self.memory_limit_mb,
        )
        outcome = run_training(backend, request)
        self.usage_ledger.record(target.name, outcome.gpu_hours, self.now_fn())
        # run_id=ambient-<target>-<record_count> dedupes bare retries: a re-run at the same
        # record_count with different metrics returns the existing candidate and discards the
        # new metrics (inherited publish_training_output idempotency).
        run_id = f"ambient-{target.name}-{manifest.record_count}"
        artifact_id = publish_candidate(
            outcome=outcome,
            target=target,
            scenario=scenario,
            registry=self.registry,
            artifacts_root=self.artifacts_root,
            run_id=run_id,
            record_count=manifest.record_count,
        )
        ctx.emitter.emit(
            "train_candidate_published",
            {"target": target.name, "artifact_id": artifact_id, "gpu_hours": outcome.gpu_hours},
            channel="ambient",
        )
        return 1

    def _already_trained(self, target_name: str, record_count: int) -> bool:
        # a candidate already exists for this (target, record_count): the dataset is unchanged
        # since it was trained, so another run would be a redundant recompute.
        return any(
            record.metadata.get("target") == target_name and record.metadata.get("record_count") == record_count
            for record in self.registry.list_all()
        )

    def _scenario_for(self, target: CharterTarget) -> str:
        # a task_family selector IS a scenario; a role selector's scenario part
        # (role@scenario) names it, else the role trains across scenarios and we
        # label the run by the role's selector head
        if target.kind == "task_family":
            return target.selector
        role, _, scenario = target.selector.partition("@")
        return scenario or role
