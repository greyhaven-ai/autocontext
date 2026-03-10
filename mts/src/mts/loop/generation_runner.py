from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from mts.agents import AgentOrchestrator
from mts.backpressure import BackpressureGate, TrendAwareGate
from mts.config import AppSettings
from mts.execution import ExecutionSupervisor
from mts.execution.executors import LocalExecutor, PrimeIntellectExecutor
from mts.harness.meta_optimizer import MetaOptimizer
from mts.integrations.primeintellect import PrimeIntellectClient
from mts.knowledge.report import generate_session_report
from mts.knowledge.trajectory import ScoreTrajectoryBuilder
from mts.loop.controller import LoopController
from mts.loop.events import EventStreamEmitter
from mts.scenarios import SCENARIO_REGISTRY
from mts.scenarios.base import ScenarioInterface
from mts.storage import ArtifactStore, SQLiteStore

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class RunSummary:
    run_id: str
    scenario: str
    generations_executed: int
    best_score: float
    current_elo: float


class GenerationRunner:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.sqlite = SQLiteStore(settings.db_path)
        self.trajectory_builder = ScoreTrajectoryBuilder(self.sqlite)
        self.artifacts = ArtifactStore(
            settings.runs_root,
            settings.knowledge_root,
            settings.skills_root,
            settings.claude_skills_path,
            max_playbook_versions=settings.playbook_max_versions,
            enable_buffered_writes=True,
        )
        self.agents = AgentOrchestrator.from_settings(
            settings, artifacts=self.artifacts, sqlite=self.sqlite,
        )
        if settings.backpressure_mode == "trend":
            self.gate: BackpressureGate | TrendAwareGate = TrendAwareGate(
                min_delta=settings.backpressure_min_delta,
                plateau_window=settings.backpressure_plateau_window,
                plateau_relaxation_factor=settings.backpressure_plateau_relaxation,
            )
        else:
            self.gate = BackpressureGate(min_delta=settings.backpressure_min_delta)
        self.remote: PrimeIntellectClient | None = None
        if settings.executor_mode == "primeintellect":
            if not settings.primeintellect_api_key:
                raise ValueError("MTS_PRIMEINTELLECT_API_KEY is required for primeintellect executor mode")
            self.remote = PrimeIntellectClient(
                api_key=settings.primeintellect_api_key or "",
                docker_image=settings.primeintellect_docker_image,
                cpu_cores=settings.primeintellect_cpu_cores,
                memory_gb=settings.primeintellect_memory_gb,
                disk_size_gb=settings.primeintellect_disk_size_gb,
                timeout_minutes=settings.primeintellect_timeout_minutes,
                max_wait_attempts=settings.primeintellect_wait_attempts,
                allow_fallback=settings.allow_primeintellect_fallback,
            )
            self.executor = ExecutionSupervisor(
                executor=PrimeIntellectExecutor(
                    self.remote,
                    max_retries=settings.primeintellect_max_retries,
                    backoff_seconds=settings.primeintellect_backoff_seconds,
                )
            )
        elif settings.executor_mode == "monty":
            # MontyExecutor: sandboxed execution via pydantic-monty interpreter.
            # Scenario methods run on host; eval script runs in Monty sandbox.
            from mts.execution.executors.monty import MontyExecutor
            self.executor = ExecutionSupervisor(
                executor=MontyExecutor(
                    max_execution_time_seconds=settings.monty_max_execution_time_seconds,
                    max_external_calls=settings.monty_max_external_calls,
                )
            )
        else:
            self.executor = ExecutionSupervisor(executor=LocalExecutor())
        self.events = EventStreamEmitter(settings.event_stream_path)
        self.controller: LoopController | None = None
        self._meta_optimizer = MetaOptimizer.from_settings(settings)

    def migrate(self, migrations_dir: Path) -> None:
        self.sqlite.migrate(migrations_dir)

    def _scenario(self, scenario_name: str) -> ScenarioInterface:
        cls = SCENARIO_REGISTRY.get(scenario_name)
        if cls is None:
            supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
            raise ValueError(f"Unknown scenario '{scenario_name}'. Supported: {supported}")
        return cls()

    def _chat_with_agent(self, role: str, message: str, prompts: object, tool_context: str) -> str:
        """One-shot chat with a specific agent role using current context."""
        try:
            if role == "competitor":
                text, _ = self.agents.competitor.run(message, tool_context=tool_context)
                return text
            elif role == "analyst":
                exec_result = self.agents.analyst.run(message)
                return exec_result.content
            elif role == "coach":
                exec_result = self.agents.coach.run(message)
                return exec_result.content
            elif role == "architect":
                exec_result = self.agents.architect.run(message)
                return exec_result.content
            else:
                return f"Unknown agent role: {role}"
        except Exception as exc:
            return f"Error chatting with {role}: {exc}"

    def _count_dead_ends(self, scenario_name: str) -> int:
        """Count dead-end entries from the dead_ends.md file."""
        content = self.artifacts.read_dead_ends(scenario_name)
        if not content:
            return 0
        return content.count("### Dead End")

    def _generate_session_report(
        self, run_id: str, scenario_name: str, duration_seconds: float,
    ) -> None:
        """Generate and persist a session report for a completed run."""
        trajectory_rows = self.sqlite.get_generation_trajectory(run_id)
        dead_ends_count = self._count_dead_ends(scenario_name)
        report = generate_session_report(
            run_id=run_id,
            scenario=scenario_name,
            trajectory_rows=trajectory_rows,
            exploration_mode=self.settings.exploration_mode,
            duration_seconds=duration_seconds,
            dead_ends_found=dead_ends_count,
        )
        markdown = report.to_markdown()
        self.artifacts.write_session_report(scenario_name, run_id, markdown)

    def run(self, scenario_name: str, generations: int, run_id: str | None = None) -> RunSummary:
        scenario = self._scenario(scenario_name)
        active_run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        run_start_time = time.monotonic()
        self.sqlite.create_run(
            active_run_id, scenario_name, generations, self.settings.executor_mode,
            agent_provider=self.settings.agent_provider,
        )
        previous_best = 0.0
        challenger_elo = 1000.0
        completed = 0
        score_history: list[float] = []
        gate_decision_history: list[str] = []
        self.events.emit("run_started", {"run_id": active_run_id, "scenario": scenario_name})

        # Seed scenario-specific tools before first generation
        if not self.artifacts.tools_dir(scenario_name).exists():
            seed = scenario.seed_tools()
            if seed:
                seed_tool_list: list[dict[str, object]] = [
                    {"name": k, "code": v, "description": f"Seed tool: {k}"} for k, v in seed.items()
                ]
                self.artifacts.persist_tools(scenario_name, 0, seed_tool_list)

        replay_narrative = ""
        coach_competitor_hints = self.artifacts.read_hints(scenario_name)

        # Cross-run knowledge inheritance: restore from best prior run if no playbook exists
        if (
            self.settings.cross_run_inheritance
            and not self.settings.ablation_no_feedback
        ):
            playbook_path = self.artifacts.knowledge_root / scenario_name / "playbook.md"
            if not playbook_path.exists():
                best_snapshot = self.sqlite.get_best_knowledge_snapshot(scenario_name)
                if best_snapshot:
                    restored = self.artifacts.restore_knowledge_snapshot(
                        scenario_name, best_snapshot["run_id"]
                    )
                    if restored:
                        LOGGER.info(
                            "restored knowledge from run %s (score=%.4f) for scenario %s",
                            best_snapshot["run_id"],
                            best_snapshot["best_score"],
                            scenario_name,
                        )

        # Harness inheritance: log existing harness files at run start
        if (
            self.settings.harness_validators_enabled
            and self.settings.harness_inheritance_enabled
        ):
            existing_harness = self.artifacts.list_harness(scenario_name)
            if existing_harness:
                LOGGER.info(
                    "inheriting %d harness file(s) for scenario %s: %s",
                    len(existing_harness), scenario_name, ", ".join(existing_harness),
                )

        try:
            for generation in range(1, generations + 1):
                if self.controller:
                    self.controller.wait_if_paused()
                    hint = self.controller.take_hint()
                    if hint:
                        coach_competitor_hints += f"\n\n[User guidance]: {hint}"
                if self.sqlite.generation_exists(active_run_id, generation):
                    LOGGER.info("generation %s already exists for run %s, skipping for idempotency", generation, active_run_id)
                    continue
                self.events.emit("generation_started", {"run_id": active_run_id, "generation": generation})
                self.sqlite.upsert_generation(
                    active_run_id,
                    generation,
                    mean_score=0.0,
                    best_score=previous_best,
                    elo=challenger_elo,
                    wins=0,
                    losses=0,
                    gate_decision="running",
                    status="running",
                )
                try:
                    from mts.loop.generation_pipeline import GenerationPipeline
                    from mts.loop.stage_types import GenerationContext

                    warm_fn = None
                    if self.settings.executor_mode == "primeintellect" and self.remote is not None:
                        def _warm(ctx_arg: object, _gen: int = generation) -> dict:
                            assert self.remote is not None
                            return self.remote.warm_provision(
                                environment_name=f"{scenario_name}-gen-{_gen}",
                                max_retries=self.settings.primeintellect_max_retries,
                                backoff_seconds=self.settings.primeintellect_backoff_seconds,
                            )
                        warm_fn = _warm

                    pipeline = GenerationPipeline(
                        orchestrator=self.agents,
                        supervisor=self.executor,
                        gate=self.gate,
                        artifacts=self.artifacts,
                        sqlite=self.sqlite,
                        trajectory_builder=self.trajectory_builder,
                        events=self.events,
                        curator=self.agents.curator,
                        controller=self.controller,
                        warm_provision_fn=warm_fn,
                        chat_with_agent_fn=self._chat_with_agent,
                        meta_optimizer=self._meta_optimizer,
                    )
                    ctx = GenerationContext(
                        run_id=active_run_id,
                        scenario_name=scenario_name,
                        scenario=scenario,
                        generation=generation,
                        settings=self.settings,
                        previous_best=previous_best,
                        challenger_elo=challenger_elo,
                        score_history=score_history,
                        gate_decision_history=gate_decision_history,
                        coach_competitor_hints=coach_competitor_hints,
                        replay_narrative=replay_narrative,
                    )
                    ctx = pipeline.run_generation(ctx)
                    previous_best = ctx.previous_best
                    challenger_elo = ctx.challenger_elo
                    replay_narrative = ctx.replay_narrative
                    coach_competitor_hints = ctx.coach_competitor_hints
                    completed += 1
                except Exception as exc:
                    self.sqlite.upsert_generation(
                        active_run_id,
                        generation,
                        mean_score=0.0,
                        best_score=previous_best,
                        elo=challenger_elo,
                        wins=0,
                        losses=0,
                        gate_decision="error",
                        status="failed",
                    )
                    self.events.emit(
                        "generation_failed",
                        {"run_id": active_run_id, "generation": generation, "error": str(exc)},
                    )
                    raise
            self.sqlite.mark_run_completed(active_run_id)
            self.artifacts.flush_writes()
        finally:
            self.artifacts.shutdown_writer()

        # Generate session report
        if self.settings.session_reports_enabled:
            duration = time.monotonic() - run_start_time
            try:
                self._generate_session_report(active_run_id, scenario_name, duration)
            except Exception:
                LOGGER.warning("failed to generate session report for run %s", active_run_id, exc_info=True)

        # Snapshot knowledge for cross-run inheritance
        if self.settings.cross_run_inheritance and not self.settings.ablation_no_feedback:
            playbook_hash = self.artifacts.snapshot_knowledge(scenario_name, active_run_id)
            self.sqlite.save_knowledge_snapshot(
                scenario=scenario_name,
                run_id=active_run_id,
                best_score=previous_best,
                best_elo=challenger_elo,
                playbook_hash=playbook_hash,
                agent_provider=self.settings.agent_provider,
                rlm_enabled=self.settings.rlm_enabled,
            )

        self.events.emit("run_completed", {"run_id": active_run_id, "completed_generations": completed})
        return RunSummary(
            run_id=active_run_id,
            scenario=scenario_name,
            generations_executed=completed,
            best_score=previous_best,
            current_elo=challenger_elo,
        )
