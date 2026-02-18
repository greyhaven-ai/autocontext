from __future__ import annotations

import dataclasses
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from mts.agents import AgentOrchestrator
from mts.backpressure import BackpressureGate, TrendAwareGate
from mts.backpressure.trend_gate import ScoreHistory
from mts.config import AppSettings
from mts.execution import ExecutionSupervisor, TournamentRunner
from mts.execution.executors import LocalExecutor, PrimeIntellectExecutor
from mts.integrations.primeintellect import PrimeIntellectClient
from mts.knowledge.trajectory import ScoreTrajectoryBuilder
from mts.loop.controller import LoopController
from mts.loop.events import EventStreamEmitter
from mts.prompts.templates import build_prompt_bundle
from mts.scenarios import SCENARIO_REGISTRY
from mts.scenarios.base import ExecutionLimits, ScenarioInterface
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
        if settings.executor_mode == "primeintellect":
            if not settings.primeintellect_api_key:
                raise ValueError("MTS_PRIMEINTELLECT_API_KEY is required for primeintellect executor mode")
            self.executor = ExecutionSupervisor(
                executor=PrimeIntellectExecutor(
                    self.remote,
                    max_retries=settings.primeintellect_max_retries,
                    backoff_seconds=settings.primeintellect_backoff_seconds,
                )
            )
        else:
            self.executor = ExecutionSupervisor(executor=LocalExecutor())
        self.tournament = TournamentRunner(self.executor)
        self.events = EventStreamEmitter(settings.event_stream_path)
        self.controller: LoopController | None = None

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

    def run(self, scenario_name: str, generations: int, run_id: str | None = None) -> RunSummary:
        scenario = self._scenario(scenario_name)
        active_run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
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
                if self.settings.use_generation_pipeline:
                    from mts.loop.generation_pipeline import GenerationPipeline
                    from mts.loop.stage_types import GenerationContext

                    warm_fn = None
                    if self.settings.executor_mode == "primeintellect":
                        def _warm(ctx_arg: object, _gen: int = generation) -> dict:
                            return self.remote.warm_provision(
                                environment_name=f"{scenario_name}-gen-{_gen}",
                                max_retries=self.settings.primeintellect_max_retries,
                                backoff_seconds=self.settings.primeintellect_backoff_seconds,
                            )
                        warm_fn = _warm

                    pipeline = GenerationPipeline(
                        orchestrator=self.agents,
                        tournament_runner=self.tournament,
                        gate=self.gate,
                        artifacts=self.artifacts,
                        sqlite=self.sqlite,
                        trajectory_builder=self.trajectory_builder,
                        events=self.events,
                        curator=self.agents.curator,
                        controller=self.controller,
                        warm_provision_fn=warm_fn,
                        chat_with_agent_fn=self._chat_with_agent,
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
                    # Sync state back from pipeline context
                    previous_best = ctx.previous_best
                    challenger_elo = ctx.challenger_elo
                    replay_narrative = ctx.replay_narrative
                    coach_competitor_hints = ctx.coach_competitor_hints
                    completed += 1
                    continue  # Skip the monolithic code below
                summary_text = f"best score so far: {previous_best:.4f}"
                state = scenario.initial_state(seed=self.settings.seed_base + generation)
                observation = scenario.get_observation(state, player_id="challenger")
                ablation = self.settings.ablation_no_feedback
                playbook = "" if ablation else self.artifacts.read_playbook(scenario_name)
                tool_context = "" if ablation else self.artifacts.read_tool_context(scenario_name)
                skills_context = "" if ablation else self.artifacts.read_skills(scenario_name)
                recent_analysis = "" if ablation else self.artifacts.read_latest_advance_analysis(scenario_name, generation)
                score_trajectory = "" if ablation else self.trajectory_builder.build_trajectory(active_run_id)
                strategy_registry = "" if ablation else self.trajectory_builder.build_strategy_registry(active_run_id)
                prompts = build_prompt_bundle(
                    scenario_rules=scenario.describe_rules(),
                    strategy_interface=scenario.describe_strategy_interface(),
                    evaluation_criteria=scenario.describe_evaluation_criteria(),
                    previous_summary=summary_text,
                    observation=observation,
                    current_playbook=playbook,
                    available_tools=tool_context,
                    operational_lessons=skills_context,
                    replay_narrative="" if ablation else replay_narrative,
                    coach_competitor_hints="" if ablation else coach_competitor_hints,
                    recent_analysis=recent_analysis,
                    score_trajectory=score_trajectory,
                    strategy_registry=strategy_registry,
                )
                if self.settings.executor_mode == "primeintellect":
                    warm_state = self.remote.warm_provision(
                        environment_name=f"{scenario_name}-gen-{generation}",
                        max_retries=self.settings.primeintellect_max_retries,
                        backoff_seconds=self.settings.primeintellect_backoff_seconds,
                    )
                    self.events.emit(
                        "primeintellect_warm_state",
                        {"run_id": active_run_id, "generation": generation, **warm_state},
                    )
                strategy_interface = scenario.describe_strategy_interface()
                roles = ["competitor", "analyst", "coach", "architect"]
                if self.agents.curator is not None:
                    roles.append("curator")
                self.events.emit("agents_started", {
                    "run_id": active_run_id, "generation": generation, "roles": roles,
                })

                def _on_role_event(role: str, status: str, _gen: int = generation) -> None:
                    self.events.emit("role_event", {
                        "run_id": active_run_id, "generation": _gen, "role": role, "status": status,
                    })

                outputs = self.agents.run_generation(
                    prompts,
                    generation_index=generation,
                    tool_context=tool_context,
                    run_id=active_run_id,
                    scenario_name=scenario_name,
                    strategy_interface=strategy_interface,
                    on_role_event=_on_role_event,
                )
                for role_exec in outputs.role_executions:
                    self.events.emit("role_completed", {
                        "run_id": active_run_id,
                        "generation": generation,
                        "role": role_exec.role,
                        "latency_ms": role_exec.usage.latency_ms,
                        "tokens": role_exec.usage.input_tokens + role_exec.usage.output_tokens,
                    })

                valid, reason = scenario.validate_actions(state, "challenger", outputs.strategy)
                if not valid:
                    raise ValueError(f"competitor strategy validation failed: {reason}")
                self.sqlite.append_agent_output(
                    active_run_id,
                    generation,
                    "competitor",
                    json.dumps(outputs.strategy, sort_keys=True),
                )
                self.sqlite.append_agent_output(active_run_id, generation, "analyst", outputs.analysis_markdown)
                self.sqlite.append_agent_output(active_run_id, generation, "coach", outputs.coach_markdown)
                self.sqlite.append_agent_output(active_run_id, generation, "architect", outputs.architect_markdown)
                for role_execution in outputs.role_executions:
                    self.sqlite.append_agent_role_metric(
                        active_run_id,
                        generation,
                        role_execution.role,
                        role_execution.usage.model,
                        role_execution.usage.input_tokens,
                        role_execution.usage.output_tokens,
                        role_execution.usage.latency_ms,
                        role_execution.subagent_id,
                        role_execution.status,
                    )
                created_tools = self.artifacts.persist_tools(scenario_name, generation, outputs.architect_tools)

                # Controller checkpoint: agent chat
                if self.controller:
                    chat_request = self.controller.poll_chat()
                    if chat_request:
                        role, message = chat_request
                        response = self._chat_with_agent(role, message, prompts, tool_context)
                        self.controller.respond_chat(role, response)

                attempt = 0
                gate_decision = "rollback"
                tournament = None
                current_strategy = outputs.strategy
                while True:
                    self.events.emit("tournament_started", {
                        "run_id": active_run_id,
                        "generation": generation,
                        "matches": self.settings.matches_per_generation,
                    })

                    def _on_match(match_index: int, score: float, _gen: int = generation) -> None:
                        self.events.emit("match_completed", {
                            "run_id": active_run_id, "generation": _gen,
                            "match_index": match_index, "score": score,
                        })

                    try:
                        tournament = self.tournament.run(
                            scenario=scenario,
                            strategy=current_strategy,
                            seed_base=self.settings.seed_base + (generation * 100) + (attempt * 10),
                            matches=self.settings.matches_per_generation,
                            limits=ExecutionLimits(),
                            challenger_elo=challenger_elo,
                            on_match=_on_match,
                        )
                    except Exception:  # pragma: no cover
                        attempt += 1
                        if attempt > self.settings.max_retries:
                            raise
                        time.sleep(self.settings.retry_backoff_seconds * attempt)
                        continue
                    if isinstance(self.gate, TrendAwareGate):
                        best_result = max(tournament.outputs, key=lambda o: o.result.score)
                        custom_metrics = scenario.custom_backpressure(best_result.result)
                        gate = self.gate.evaluate(
                            previous_best,
                            tournament.best_score,
                            retry_count=attempt,
                            max_retries=self.settings.max_retries,
                            history=ScoreHistory(
                                scores=tuple(score_history),
                                gate_decisions=tuple(gate_decision_history),
                            ),
                            custom_metrics=custom_metrics,
                        )
                    else:
                        gate = self.gate.evaluate(
                            previous_best,
                            tournament.best_score,
                            retry_count=attempt,
                            max_retries=self.settings.max_retries,
                        )
                    gate_decision = gate.decision
                    if gate_decision == "retry":
                        attempt += 1
                        self.sqlite.append_recovery_marker(active_run_id, generation, gate_decision, gate.reason, attempt)
                        if attempt > self.settings.max_retries:
                            gate_decision = "rollback"
                            break
                        # Retry learning: re-invoke competitor with failure context
                        retry_prompt = (
                            prompts.competitor
                            + f"\n\n--- RETRY ATTEMPT {attempt} ---\n"
                            f"Your previous strategy scored {tournament.best_score:.4f} "
                            f"but needed delta >= {self.settings.backpressure_min_delta} over {previous_best:.4f}.\n"
                            f"Previous strategy: {json.dumps(current_strategy, sort_keys=True)}\n"
                            f"Adjust your strategy to improve. Do not repeat the same approach.\n"
                        )
                        try:
                            raw_text, _ = self.agents.competitor.run(retry_prompt, tool_context=tool_context)
                            revised_strategy, _ = self.agents.translator.translate(raw_text, strategy_interface)
                            valid, reason = scenario.validate_actions(state, "challenger", revised_strategy)
                            if valid:
                                current_strategy = revised_strategy
                        except Exception:
                            pass  # Fall back to current strategy
                        time.sleep(self.settings.retry_backoff_seconds * attempt)
                        continue
                    self.sqlite.append_recovery_marker(active_run_id, generation, gate_decision, gate.reason, attempt)
                    break

                assert tournament is not None
                self.events.emit("tournament_completed", {
                    "run_id": active_run_id, "generation": generation,
                    "mean_score": tournament.mean_score, "best_score": tournament.best_score,
                    "wins": tournament.wins, "losses": tournament.losses,
                })
                gate_delta = round(tournament.best_score - previous_best, 6)
                self.events.emit("gate_decided", {
                    "run_id": active_run_id, "generation": generation,
                    "decision": gate_decision, "delta": gate_delta,
                })

                # Controller checkpoint: gate override
                if self.controller:
                    override = self.controller.take_gate_override()
                    if override:
                        gate_decision = override
                # Generate replay narrative from best match for next generation
                best_output = max(tournament.outputs, key=lambda o: o.result.score)
                replay_narrative = scenario.replay_to_narrative(best_output.result.replay)
                gen_dir = self.artifacts.generation_dir(active_run_id, generation)
                self.artifacts.write_markdown(gen_dir / "narrative.md", replay_narrative)

                # Accumulate history for trend-aware gate
                score_history.append(tournament.best_score)
                gate_decision_history.append(gate_decision)

                if gate_decision == "advance":
                    previous_best = max(previous_best, tournament.best_score)
                    challenger_elo = tournament.elo_after

                # Curator quality gate: assess playbook before persisting
                if (
                    gate_decision == "advance"
                    and self.agents.curator is not None
                    and outputs.coach_playbook
                    and not self.settings.ablation_no_feedback
                ):
                    current_pb = self.artifacts.read_playbook(scenario_name)
                    if current_pb and current_pb != "No playbook yet. Start from scenario rules and observation.":
                        self.events.emit("curator_started", {
                            "run_id": active_run_id, "generation": generation,
                        })
                        curator_trajectory = self.trajectory_builder.build_trajectory(active_run_id)
                        curator_analysis = self.artifacts.read_latest_advance_analysis(scenario_name, generation)
                        curator_decision, curator_exec = self.agents.curator.assess_playbook_quality(
                            current_playbook=current_pb,
                            proposed_playbook=outputs.coach_playbook,
                            score_trajectory=curator_trajectory,
                            recent_analysis=curator_analysis,
                        )
                        self.sqlite.append_agent_output(
                            active_run_id, generation, "curator", curator_exec.content,
                        )
                        self.sqlite.append_agent_role_metric(
                            active_run_id, generation, curator_exec.role, curator_exec.usage.model,
                            curator_exec.usage.input_tokens, curator_exec.usage.output_tokens,
                            curator_exec.usage.latency_ms, curator_exec.subagent_id, curator_exec.status,
                        )
                        if curator_decision.decision == "reject":
                            outputs = dataclasses.replace(outputs, coach_playbook="")
                        elif curator_decision.decision == "merge" and curator_decision.playbook:
                            outputs = dataclasses.replace(outputs, coach_playbook=curator_decision.playbook)
                        # "accept" → no change to outputs
                        self.events.emit("curator_completed", {
                            "run_id": active_run_id, "generation": generation,
                            "decision": curator_decision.decision,
                        })

                metrics = {
                    "generation_index": generation,
                    "mean_score": tournament.mean_score,
                    "best_score": previous_best,
                    "elo": challenger_elo,
                    "wins": tournament.wins,
                    "losses": tournament.losses,
                    "runs": self.settings.matches_per_generation,
                    "gate_decision": gate_decision,
                    "gate_delta": gate_delta,
                    "gate_threshold": self.settings.backpressure_min_delta,
                }
                for idx, match_output in enumerate(tournament.outputs):
                    self.sqlite.insert_match(
                        active_run_id,
                        generation,
                        self.settings.seed_base + (generation * 100) + idx,
                        match_output.result.score,
                        match_output.result.passed_validation,
                        json.dumps(match_output.result.validation_errors),
                    )
                self.sqlite.upsert_generation(
                    active_run_id,
                    generation,
                    mean_score=tournament.mean_score,
                    best_score=previous_best,
                    elo=challenger_elo,
                    wins=tournament.wins,
                    losses=tournament.losses,
                    gate_decision=gate_decision,
                    status="completed",
                )
                # Gate-aware persistence: only update playbook on advance
                self.artifacts.persist_generation(
                    run_id=active_run_id,
                    generation_index=generation,
                    metrics=metrics,
                    replay_payload=tournament.outputs[0].replay.model_dump(),
                    analysis_md=outputs.analysis_markdown,
                    coach_md=outputs.coach_markdown,
                    architect_md=outputs.architect_markdown,
                    scenario_name=scenario_name,
                    coach_playbook=outputs.coach_playbook if gate_decision == "advance" else "",
                )
                if gate_decision == "advance":
                    skill_lessons = outputs.coach_lessons
                else:
                    retry_note = f" after {attempt} retries" if attempt > 0 else ""
                    skill_lessons = (
                        f"- Generation {generation} ROLLBACK{retry_note} "
                        f"(score={tournament.best_score:.4f}, "
                        f"delta={gate_delta:+.4f}, threshold={self.settings.backpressure_min_delta}). "
                        f"Strategy: {json.dumps(current_strategy, sort_keys=True)[:200]}. "
                        f"Narrative: {replay_narrative[:150]}. "
                        f"Avoid this approach."
                    )
                self.artifacts.persist_skill_note(
                    scenario_name=scenario_name,
                    generation_index=generation,
                    decision=gate_decision,
                    lessons=skill_lessons,
                )
                # Curator lesson consolidation
                existing_lessons_check = self.artifacts.read_skill_lessons_raw(scenario_name)
                severely_over = len(existing_lessons_check) > self.settings.skill_max_lessons * 2
                if (
                    self.agents.curator is not None
                    and self.settings.curator_enabled
                    and (generation % self.settings.curator_consolidate_every_n_gens == 0 or severely_over)
                    and not self.settings.ablation_no_feedback
                ):
                    existing_lessons = self.artifacts.read_skill_lessons_raw(scenario_name)
                    if len(existing_lessons) > self.settings.skill_max_lessons:
                        consolidation_trajectory = self.trajectory_builder.build_trajectory(active_run_id)
                        lesson_result, lesson_exec = self.agents.curator.consolidate_lessons(
                            existing_lessons, self.settings.skill_max_lessons, consolidation_trajectory,
                        )
                        self.artifacts.replace_skill_lessons(scenario_name, lesson_result.consolidated_lessons)
                        self.sqlite.append_agent_output(
                            active_run_id, generation, "curator_consolidation", lesson_exec.content,
                        )
                        self.sqlite.append_agent_role_metric(
                            active_run_id, generation, lesson_exec.role, lesson_exec.usage.model,
                            lesson_exec.usage.input_tokens, lesson_exec.usage.output_tokens,
                            lesson_exec.usage.latency_ms, lesson_exec.subagent_id, lesson_exec.status,
                        )
                # Carry forward coach hints for next generation's competitor prompt
                coach_competitor_hints = outputs.coach_competitor_hints
                if gate_decision == "advance" and coach_competitor_hints:
                    self.artifacts.write_hints(scenario_name, coach_competitor_hints)
                completed += 1
                self.events.emit(
                    "generation_completed",
                    {
                        "run_id": active_run_id,
                        "generation": generation,
                        "mean_score": tournament.mean_score,
                        "best_score": previous_best,
                        "elo": challenger_elo,
                        "gate_decision": gate_decision,
                        "created_tools": created_tools,
                    },
                )
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
