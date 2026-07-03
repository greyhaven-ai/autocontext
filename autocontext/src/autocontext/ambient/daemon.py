"""the ambient daemon: orchestrates the five stages with per-stage breakers."""

from __future__ import annotations

import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager

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

    def _context(self) -> StageContext:
        return StageContext(charter=self.charter, queue=self.queue, emitter=self.emitter)

    def run_stage_once(self, stage_name: str) -> StageResult:
        stage = self._stages[stage_name]
        breaker = self._breakers[stage_name]
        was_paused = breaker.paused
        try:
            result = stage.run_once(self._context())
        except Exception as exc:
            breaker.record_exception()
            self.emitter.emit("stage_failed", {"stage": stage_name, "error": str(exc)}, channel="ambient")
            if breaker.paused and not was_paused:
                self.emitter.emit("stage_paused", {"stage": stage_name}, channel="ambient")
            return StageResult(errors=1)
        breaker.record(result)
        self.emitter.emit(
            "stage_completed",
            {"stage": stage_name, "processed": result.processed, "errors": result.errors},
            channel="ambient",
        )
        # a stage that reports errors without raising can also trip the
        # breaker; emit the pause transition here too so alerting never
        # misses an auto-pause
        if breaker.paused and not was_paused:
            self.emitter.emit("stage_paused", {"stage": stage_name}, channel="ambient")
        return result

    def run_cycle(self) -> dict[str, StageResult]:
        results: dict[str, StageResult] = {}
        for name in self._stages:
            if self._breakers[name].paused:
                continue
            results[name] = self.run_stage_once(name)
        return results

    @contextmanager
    def _exclusive_daemon_lock(self) -> Iterator[None]:
        # exactly one resident daemon per queue database: without this, a
        # second daemon's startup requeue would hand the first daemon's
        # in-flight jobs out again (double-claim)
        lock_path = self.queue.db_path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("w")
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError(
                    f"another ambient daemon already holds {lock_path}; refusing to start a second resident loop"
                ) from exc
            yield
        finally:
            handle.close()

    def run_forever(self, poll_seconds: float, max_cycles: int | None = None) -> None:
        if poll_seconds < 0:
            raise ValueError("poll_seconds must be zero or positive")
        with self._exclusive_daemon_lock():
            # crash recovery lives here, not in the constructor: read-only
            # commands (status) and manual one-shots construct a daemon too,
            # and must never yank another process's in-flight jobs back to
            # pending; the exclusive lock above makes the requeue safe
            requeued = self.queue.requeue_stale_running()
            if requeued:
                self.emitter.emit("stale_jobs_requeued", {"count": requeued}, channel="ambient")
            cycles = 0
            while max_cycles is None or cycles < max_cycles:
                self.run_cycle()
                cycles += 1
                if poll_seconds > 0:
                    time.sleep(poll_seconds)

    def status(self) -> dict[str, dict[str, object]]:
        """per-stage view: paused reflects only this process's breakers, and
        queue_depth counts pending backlog only (in-flight jobs are invisible).
        """
        return {name: {"paused": self._breakers[name].paused, "queue_depth": self.queue.depth(name)} for name in self._stages}
