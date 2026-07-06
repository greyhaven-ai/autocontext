"""stage framework: the contract every ambient stage implements, plus the auto-pause breaker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from autocontext.ambient.charter import Charter
from autocontext.ambient.proposals import ProposalStore
from autocontext.ambient.queue import AmbientQueue
from autocontext.harness.core.events import EventStreamEmitter

STAGE_NAMES: tuple[str, ...] = ("ingest", "curate", "advise", "train", "evaluate", "promote")


@dataclass(slots=True)
class StageResult:
    processed: int = 0
    errors: int = 0


@dataclass(slots=True)
class StageContext:
    charter: Charter
    queue: AmbientQueue
    emitter: EventStreamEmitter
    proposal_store: ProposalStore | None = None


class Stage(Protocol):
    name: str

    def run_once(self, ctx: StageContext) -> StageResult: ...


@dataclass(slots=True)
class AutoPauseBreaker:
    threshold: int
    consecutive_failures: int = field(default=0)

    def record(self, result: StageResult) -> None:
        if result.errors > 0:
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0

    def record_exception(self) -> None:
        self.consecutive_failures += 1

    @property
    def paused(self) -> bool:
        return self.consecutive_failures >= self.threshold


@dataclass(slots=True)
class NoOpStage:
    name: str

    def run_once(self, ctx: StageContext) -> StageResult:
        return StageResult()
