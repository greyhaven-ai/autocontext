from __future__ import annotations

import difflib
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from autocontext.config import AppSettings
from autocontext.loop.events import EventStreamEmitter
from autocontext.loop.generation_runner import GenerationRunner, RunSummary
from autocontext.storage import ArtifactStore, SQLiteStore
from autocontext.storage.artifacts import EMPTY_PLAYBOOK_SENTINEL

logger = logging.getLogger(__name__)


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


def compute_playbook_divergence(before: str, after: str) -> float:
    """Compute divergence between two playbook versions.

    Returns 0.0 for identical, 1.0 for completely different.
    Uses SequenceMatcher ratio (similarity), inverted to divergence.
    """
    # Treat the default sentinel as empty to avoid false high-divergence on first runs
    if before == EMPTY_PLAYBOOK_SENTINEL:
        before = ""
    if after == EMPTY_PLAYBOOK_SENTINEL:
        after = ""
    if not before and not after:
        return 0.0
    if not before or not after:
        return 1.0
    similarity = difflib.SequenceMatcher(None, before, after).ratio()
    return round(1.0 - similarity, 4)


def detect_oscillation(
    divergence_history: list[float],
    threshold: float,
    window: int,
) -> bool:
    """Detect playbook oscillation from divergence history.

    Returns True if the last `window` entries all exceed `threshold`.
    """
    if len(divergence_history) < window:
        return False
    recent = divergence_history[-window:]
    return all(d > threshold for d in recent)


class EcosystemRunner:
    def __init__(self, base_settings: AppSettings, config: EcosystemConfig) -> None:
        self.base_settings = base_settings
        self.config = config
        self.events = EventStreamEmitter(base_settings.event_stream_path)
        self._divergence_history: list[float] = []
        self._locked = False
        self._artifacts: ArtifactStore | None = None

    def _get_artifacts(self) -> ArtifactStore:
        """Lazy-initialized shared ArtifactStore for convergence tracking."""
        if self._artifacts is None:
            self._artifacts = ArtifactStore(
                self.base_settings.runs_root,
                self.base_settings.knowledge_root,
                self.base_settings.skills_root,
                self.base_settings.claude_skills_path,
            )
        return self._artifacts

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

        # Read initial playbook state for convergence tracking
        _pre_playbook = ""
        if self.base_settings.ecosystem_convergence_enabled:
            _pre_playbook = self._get_artifacts().read_playbook(self.config.scenario)

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

                logger.info(
                    "ecosystem cycle=%d phase=%d provider=%s rlm=%s gens=%d run_id=%s",
                    cycle, phase_idx, phase.provider, phase.rlm_enabled, phase.generations, run_id,
                )

                summary = runner.run(
                    scenario_name=self.config.scenario,
                    generations=phase.generations,
                    run_id=run_id,
                )
                summaries.append(summary)

                # Convergence detection
                if (
                    self.base_settings.ecosystem_convergence_enabled
                    and not self._locked
                ):
                    post_playbook = self._get_artifacts().read_playbook(self.config.scenario)
                    divergence = compute_playbook_divergence(_pre_playbook, post_playbook)
                    self._divergence_history.append(divergence)

                    if detect_oscillation(
                        self._divergence_history,
                        threshold=self.base_settings.ecosystem_divergence_threshold,
                        window=self.base_settings.ecosystem_oscillation_window,
                    ):
                        self._locked = True
                        self.events.emit(
                            "ecosystem_convergence_locked",
                            {
                                "scenario": self.config.scenario,
                                "cycle": cycle,
                                "divergence_history": self._divergence_history,
                            },
                            channel="ecosystem",
                        )
                        logger.warning(
                            "ecosystem convergence lock: playbook oscillating for %d cycles",
                            self.base_settings.ecosystem_oscillation_window,
                        )
                    _pre_playbook = post_playbook

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
