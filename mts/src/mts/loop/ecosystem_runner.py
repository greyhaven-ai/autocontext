from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from mts.config import AppSettings
from mts.loop.events import EventStreamEmitter
from mts.loop.generation_runner import GenerationRunner, RunSummary
from mts.storage import SQLiteStore

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class EcosystemPhase:
    provider: str
    rlm_enabled: bool
    generations: int


@dataclass(slots=True)
class EcosystemConfig:
    scenario: str
    cycles: int
    gens_per_cycle: int
    phases: list[EcosystemPhase] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.phases:
            self.phases = [
                EcosystemPhase(provider="anthropic", rlm_enabled=True, generations=self.gens_per_cycle),
                EcosystemPhase(provider="agent_sdk", rlm_enabled=False, generations=self.gens_per_cycle),
            ]


@dataclass(slots=True)
class EcosystemSummary:
    run_summaries: list[RunSummary]
    scenario: str
    cycles: int

    def score_trajectory(self) -> list[tuple[str, float]]:
        return [(rs.run_id, rs.best_score) for rs in self.run_summaries]


class EcosystemRunner:
    def __init__(self, base_settings: AppSettings, config: EcosystemConfig) -> None:
        self.base_settings = base_settings
        self.config = config
        self.events = EventStreamEmitter(base_settings.event_stream_path)

    def migrate(self, migrations_dir: Path) -> None:
        store = SQLiteStore(self.base_settings.db_path)
        store.migrate(migrations_dir)

    def _make_run_id(self, scenario: str, cycle: int, phase_index: int) -> str:
        return f"eco_{scenario}_c{cycle}_p{phase_index}_{uuid.uuid4().hex[:8]}"

    def _phase_settings(self, phase: EcosystemPhase) -> AppSettings:
        return self.base_settings.model_copy(update={
            "agent_provider": phase.provider,
            "rlm_enabled": phase.rlm_enabled,
        })

    def run(self) -> EcosystemSummary:
        migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
        summaries: list[RunSummary] = []

        self.events.emit(
            "ecosystem_started",
            {
                "scenario": self.config.scenario,
                "cycles": self.config.cycles,
                "phases": len(self.config.phases),
            },
            channel="ecosystem",
        )

        for cycle in range(1, self.config.cycles + 1):
            self.events.emit(
                "ecosystem_cycle_started",
                {"cycle": cycle, "scenario": self.config.scenario},
                channel="ecosystem",
            )

            for phase_idx, phase in enumerate(self.config.phases):
                run_id = self._make_run_id(self.config.scenario, cycle, phase_idx)
                phase_settings = self._phase_settings(phase)
                runner = GenerationRunner(phase_settings)
                runner.migrate(migrations_dir)

                LOGGER.info(
                    "ecosystem cycle=%d phase=%d provider=%s rlm=%s gens=%d run_id=%s",
                    cycle, phase_idx, phase.provider, phase.rlm_enabled, phase.generations, run_id,
                )

                summary = runner.run(
                    scenario_name=self.config.scenario,
                    generations=phase.generations,
                    run_id=run_id,
                )
                summaries.append(summary)

            self.events.emit(
                "ecosystem_cycle_completed",
                {"cycle": cycle, "scenario": self.config.scenario},
                channel="ecosystem",
            )

        self.events.emit(
            "ecosystem_completed",
            {
                "scenario": self.config.scenario,
                "total_runs": len(summaries),
                "cycles": self.config.cycles,
            },
            channel="ecosystem",
        )

        return EcosystemSummary(
            run_summaries=summaries,
            scenario=self.config.scenario,
            cycles=self.config.cycles,
        )
