"""the ambient daemon: orchestrates the five stages with per-stage breakers."""

from __future__ import annotations

import time

from autocontext.ambient.charter import Charter
from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.stage import (
    STAGE_NAMES,
    AutoPauseBreaker,
    NoOpStage,
    Stage,
    StageContext,
    StageResult,
)
from autocontext.harness.core.events import EventStreamEmitter


class AmbientDaemon:
    def __init__(
        self,
        charter: Charter,
        queue: AmbientQueue,
        emitter: EventStreamEmitter,
        stages: dict[str, Stage] | None = None,
        breaker_threshold: int = 3,
    ) -> None:
        self.charter = charter
        self.queue = queue
        self.emitter = emitter
        self._stages: dict[str, Stage] = stages if stages is not None else {name: NoOpStage(name=name) for name in STAGE_NAMES}
        self._breakers: dict[str, AutoPauseBreaker] = {
            name: AutoPauseBreaker(threshold=breaker_threshold) for name in self._stages
        }
        # crash recovery: jobs stuck in running from a previous process
        # return to pending before the first cycle
        requeued = self.queue.requeue_stale_running()
        if requeued:
            self.emitter.emit("stale_jobs_requeued", {"count": requeued}, channel="ambient")

    def _context(self) -> StageContext:
        return StageContext(charter=self.charter, queue=self.queue, emitter=self.emitter)

    def run_stage_once(self, stage_name: str) -> StageResult:
        stage = self._stages[stage_name]
        breaker = self._breakers[stage_name]
        try:
            result = stage.run_once(self._context())
        except Exception as exc:
            breaker.record_exception()
            self.emitter.emit("stage_failed", {"stage": stage_name, "error": str(exc)}, channel="ambient")
            if breaker.paused:
                self.emitter.emit("stage_paused", {"stage": stage_name}, channel="ambient")
            return StageResult(errors=1)
        breaker.record(result)
        self.emitter.emit(
            "stage_completed",
            {"stage": stage_name, "processed": result.processed, "errors": result.errors},
            channel="ambient",
        )
        return result

    def run_cycle(self) -> dict[str, StageResult]:
        results: dict[str, StageResult] = {}
        for name in self._stages:
            if self._breakers[name].paused:
                continue
            results[name] = self.run_stage_once(name)
        return results

    def run_forever(self, poll_seconds: float, max_cycles: int | None = None) -> None:
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            self.run_cycle()
            cycles += 1
            if poll_seconds > 0:
                time.sleep(poll_seconds)

    def status(self) -> dict[str, dict[str, object]]:
        return {name: {"paused": self._breakers[name].paused, "queue_depth": self.queue.depth(name)} for name in self._stages}
