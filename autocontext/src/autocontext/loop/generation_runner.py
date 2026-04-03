from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, cast

from autocontext import __version__ as package_fallback_version
from autocontext.agents import AgentOrchestrator
from autocontext.analytics.aggregate_runner import AggregateRunner
from autocontext.analytics.calibration import (
    CalibrationRound,
    CalibrationStore,
    SpotCheckSampler,
)
from autocontext.analytics.clustering import PatternClusterer
from autocontext.analytics.correlation import CorrelationStore
from autocontext.analytics.credit_assignment import summarize_credit_patterns
from autocontext.analytics.extractor import FacetExtractor
from autocontext.analytics.facets import RunFacet
from autocontext.analytics.issue_store import IssueStore
from autocontext.analytics.regression_fixtures import FixtureStore, generate_fixtures_from_friction
from autocontext.analytics.rubric_drift import DriftStore, RubricDriftMonitor, RubricSnapshot
from autocontext.analytics.run_trace import (
    ActorRef,
    CausalEdge,
    ResourceRef,
    RunTrace,
    TraceEvent,
    TraceStore,
)
from autocontext.analytics.store import FacetStore
from autocontext.analytics.taxonomy import FacetTaxonomy
from autocontext.analytics.timeline_inspector import StateInspector, TimelineBuilder
from autocontext.analytics.trace_reporter import ReportStore, TraceReporter
from autocontext.config import AppSettings
from autocontext.execution import ExecutionSupervisor
from autocontext.execution.executors import LocalExecutor, PrimeIntellectExecutor
from autocontext.harness.meta_optimizer import MetaOptimizer
from autocontext.harness.pipeline.gate import BackpressureGate
from autocontext.harness.pipeline.trend_gate import TrendAwareGate
from autocontext.harness.scoring.backends import get_backend
from autocontext.integrations.primeintellect import PrimeIntellectClient
from autocontext.knowledge.mutation_log import MutationEntry
from autocontext.knowledge.normalized_metrics import generate_run_progress_report
from autocontext.knowledge.report import generate_session_report
from autocontext.knowledge.trajectory import ScoreTrajectoryBuilder
from autocontext.knowledge.weakness import WeaknessAnalyzer
from autocontext.loop.controller import LoopController
from autocontext.loop.events import EventStreamEmitter
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.families import detect_family
from autocontext.storage import ArtifactStore, SQLiteStore

logger = logging.getLogger(__name__)


def _current_release_version() -> str:
    """Return the installed package version used for aggregate release correlation."""
    try:
        return package_version("autoctx")
    except PackageNotFoundError:
        return package_fallback_version


@dataclass(slots=True)
class RunSummary:
    run_id: str
    scenario: str
    generations_executed: int
    best_score: float
    current_elo: float


class GenerationRunner:
    def __init__(self, settings: AppSettings) -> None:
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
                raise ValueError("AUTOCONTEXT_PRIMEINTELLECT_API_KEY is required for primeintellect executor mode")
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
            from autocontext.execution.executors.monty import MontyExecutor
            self.executor = ExecutionSupervisor(
                executor=MontyExecutor(
                    max_execution_time_seconds=settings.monty_max_execution_time_seconds,
                    max_external_calls=settings.monty_max_external_calls,
                )
            )
        elif settings.executor_mode == "ssh":
            if not settings.ssh_host:
                raise ValueError("AUTOCONTEXT_SSH_HOST is required for ssh executor mode")
            from autocontext.execution.executors.ssh import SSHExecutor
            from autocontext.integrations.ssh.client import SSHClient
            from autocontext.integrations.ssh.config import SSHHostConfig

            ssh_config = SSHHostConfig(
                name=settings.ssh_host,
                hostname=settings.ssh_host,
                port=settings.ssh_port,
                user=settings.ssh_user,
                identity_file=settings.ssh_identity_file,
                working_directory=settings.ssh_working_directory,
                connect_timeout=settings.ssh_connect_timeout,
                command_timeout=settings.ssh_command_timeout,
            )
            ssh_client = SSHClient(ssh_config)
            try:
                ssh_client.validate_runtime()
            except RuntimeError as exc:
                if not settings.ssh_allow_fallback:
                    raise
                logger.warning("SSH executor preflight failed; falling back to local executor: %s", exc)
                self.executor = ExecutionSupervisor(executor=LocalExecutor())
            else:
                self.executor = ExecutionSupervisor(
                    executor=SSHExecutor(
                        client=ssh_client,
                        allow_fallback=settings.ssh_allow_fallback,
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
        return cast(ScenarioInterface, cls())

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
            logger.debug("loop.generation_runner: caught Exception", exc_info=True)
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
        current_generation = max(
            (int(row.get("generation_index", 0)) for row in trajectory_rows),
            default=0,
        )
        structured_lessons = self.artifacts.lesson_store.read_lessons(scenario_name)
        stale_lessons_count = 0
        superseded_lessons_count = 0
        if structured_lessons:
            stale_lessons_count = len(
                self.artifacts.lesson_store.get_stale_lessons(
                    scenario_name,
                    current_generation=current_generation,
                )
            )
            superseded_lessons_count = sum(
                1 for lesson in structured_lessons if lesson.is_superseded()
            )
        report = generate_session_report(
            run_id=run_id,
            scenario=scenario_name,
            trajectory_rows=trajectory_rows,
            exploration_mode=self.settings.exploration_mode,
            duration_seconds=duration_seconds,
            dead_ends_found=dead_ends_count,
            stale_lessons_count=stale_lessons_count,
            superseded_lessons_count=superseded_lessons_count,
        )
        markdown = report.to_markdown()
        self.artifacts.write_session_report(scenario_name, run_id, markdown)

    def _generate_trace_grounded_reports(self, run_id: str, scenario_name: str) -> None:
        """Generate trace-backed writeups and weakness reports for a completed run.

        Falls back to the legacy weakness analyzer if no trace artifact exists yet.
        """
        analytics_root = self.settings.knowledge_root / "analytics"
        trace = TraceStore(analytics_root).load(f"trace-{run_id}")

        if trace is not None:
            reporter = TraceReporter()
            report_store = ReportStore(analytics_root)
            writeup = reporter.generate_writeup(trace)
            weakness_report = reporter.generate_weakness_report(trace)
            report_store.persist_writeup(writeup)
            report_store.persist_weakness_report(weakness_report)
            self.artifacts.write_weakness_report(scenario_name, run_id, weakness_report)
            return

        trajectory_rows = self.sqlite.get_generation_trajectory(run_id)
        match_rows = self.sqlite.get_matches_for_run(run_id)
        analyzer = WeaknessAnalyzer()
        legacy_report = analyzer.analyze(
            run_id=run_id,
            scenario=scenario_name,
            trajectory=trajectory_rows,
            match_data=match_rows,
        )
        self.artifacts.write_weakness_report(scenario_name, run_id, legacy_report)

    def _generate_progress_report(self, run_id: str, scenario_name: str) -> None:
        """Generate and persist a normalized progress report for a completed run."""
        trajectory_rows = self.sqlite.get_generation_trajectory(run_id)
        role_metrics = self.sqlite.get_agent_role_metrics(run_id)
        consultation_cost = self.sqlite.get_total_consultation_cost(run_id)
        report = generate_run_progress_report(
            run_id=run_id,
            scenario=scenario_name,
            trajectory=trajectory_rows,
            role_metrics=role_metrics,
            consultation_cost=consultation_cost,
        )
        self.artifacts.write_progress_report(scenario_name, run_id, report)

    def _generate_aggregate_analytics(
        self,
        run_id: str,
        scenario_name: str,
        scenario: ScenarioInterface,
    ) -> None:
        """Extract and persist aggregate run facets, then update clusters/taxonomy."""
        family = detect_family(scenario)
        run_payload = {
            "run_id": run_id,
            "scenario": scenario_name,
            "scenario_family": family.name if family is not None else "",
            "agent_provider": self.settings.agent_provider,
            "executor_mode": self.settings.executor_mode,
            "metadata": {
                "exploration_mode": self.settings.exploration_mode,
                "rlm_enabled": self.settings.rlm_enabled,
                "release": _current_release_version(),
            },
        }
        generation_rows = self.sqlite.get_generation_metrics(run_id)
        role_metrics = self.sqlite.get_agent_role_metrics(run_id)
        staged_validations = self.sqlite.get_staged_validation_results_for_run(run_id)
        consultations = self.sqlite.get_consultations_for_run(run_id)
        recovery = self.sqlite.get_recovery_markers_for_run(run_id)

        facet = FacetExtractor().extract({
            "run": run_payload,
            "generations": generation_rows,
            "role_metrics": role_metrics,
            "staged_validations": staged_validations,
            "consultations": consultations,
            "recovery": recovery,
        })

        facet_store = FacetStore(self.settings.knowledge_root)
        facet_store.persist(facet)

        all_facets = facet_store.list_facets()
        clusterer = PatternClusterer()
        friction_clusters = clusterer.cluster_friction(all_facets)
        delight_clusters = clusterer.cluster_delight(all_facets)

        analytics_root = self.settings.knowledge_root / "analytics"
        analytics_root.mkdir(parents=True, exist_ok=True)
        (analytics_root / "friction_clusters.json").write_text(
            json.dumps([cluster.to_dict() for cluster in friction_clusters], indent=2),
            encoding="utf-8",
        )
        (analytics_root / "delight_clusters.json").write_text(
            json.dumps([cluster.to_dict() for cluster in delight_clusters], indent=2),
            encoding="utf-8",
        )
        credit_patterns_dir = analytics_root / "credit_assignment_patterns"
        credit_patterns_dir.mkdir(parents=True, exist_ok=True)
        credit_pattern_payload = summarize_credit_patterns(
            self.artifacts.list_credit_assignments(scenario_name),
        )
        (credit_patterns_dir / f"{scenario_name}.json").write_text(
            json.dumps(credit_pattern_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        if self.settings.regression_fixtures_enabled:
            relevant_friction = clusterer.query_clusters(
                friction_clusters,
                scenario=scenario_name,
                agent_provider=self.settings.agent_provider,
                scenario_family=family.name if family is not None else None,
            )
            fixtures = generate_fixtures_from_friction(
                [cluster.to_dict() for cluster in relevant_friction],
                scenario=scenario_name,
                min_occurrences=self.settings.regression_fixture_min_occurrences,
            )
            FixtureStore(analytics_root).replace_for_scenario(scenario_name, fixtures)

        taxonomy_path = analytics_root / "taxonomy.json"
        taxonomy = FacetTaxonomy.load(taxonomy_path)
        taxonomy.evolve([*friction_clusters, *delight_clusters])
        taxonomy.save(taxonomy_path)

        aggregate_runner = AggregateRunner(
            facet_store=facet_store,
            correlation_store=CorrelationStore(analytics_root),
            issue_store=IssueStore(analytics_root),
        )
        aggregate_runner.run()
        self._generate_rubric_drift_and_calibration(
            analytics_root=analytics_root,
            facets=all_facets,
            scenario_family=family.name if family is not None else "",
        )

    def _generate_rubric_drift_and_calibration(
        self,
        *,
        analytics_root: Path,
        facets: list[RunFacet],
        scenario_family: str,
    ) -> None:
        """Persist rubric-drift warnings and pending human calibration rounds."""
        current_release = _current_release_version()
        current_provider = self.settings.agent_provider
        relevant_facets = [
            facet for facet in facets
            if getattr(facet, "scenario_family", "") == scenario_family
            and getattr(facet, "agent_provider", "") == current_provider
            and getattr(facet, "metadata", {}).get("release", "") == current_release
        ]
        if not relevant_facets:
            return

        drift_store = DriftStore(analytics_root)
        baseline = self._latest_drift_baseline(
            drift_store.list_snapshots(),
            release=current_release,
            scenario_family=scenario_family,
            agent_provider=current_provider,
        )
        monitor = RubricDriftMonitor()
        snapshot, warnings = monitor.analyze(
            relevant_facets,
            release=current_release,
            scenario_family=scenario_family,
            agent_provider=current_provider,
            baseline=baseline,
        )
        drift_store.persist_snapshot(snapshot)
        for warning in warnings:
            drift_store.persist_warning(warning)

        if not warnings:
            return

        calibration_store = CalibrationStore(analytics_root)
        if self._has_pending_calibration_round(
            calibration_store,
            release=current_release,
            scenario_family=scenario_family,
            agent_provider=current_provider,
        ):
            return

        samples = SpotCheckSampler(max_samples=5).sample(
            relevant_facets,
            drift_warnings=warnings,
        )
        if not samples:
            return

        warning_types = sorted({warning.warning_type for warning in warnings})
        calibration_store.persist_round(
            CalibrationRound(
                round_id=f"round-{uuid.uuid4().hex[:8]}",
                created_at=snapshot.created_at,
                samples=samples,
                outcomes=[],
                status="pending",
                summary=(
                    f"{len(samples)} high-risk sample(s) selected from "
                    f"{len(relevant_facets)} run(s); warnings: {', '.join(warning_types)}"
                ),
                metadata={
                    "snapshot_id": snapshot.snapshot_id,
                    "release": current_release,
                    "scenario_family": scenario_family,
                    "agent_provider": current_provider,
                    "warning_ids": [warning.warning_id for warning in warnings],
                },
            )
        )

    def _latest_drift_baseline(
        self,
        snapshots: list[RubricSnapshot],
        *,
        release: str,
        scenario_family: str,
        agent_provider: str,
    ) -> RubricSnapshot | None:
        candidates = [
            snapshot for snapshot in snapshots
            if snapshot.scenario_family == scenario_family
            and snapshot.agent_provider == agent_provider
            and snapshot.release
            and snapshot.release != release
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda snapshot: snapshot.window_end or snapshot.created_at,
        )

    def _has_pending_calibration_round(
        self,
        calibration_store: CalibrationStore,
        *,
        release: str,
        scenario_family: str,
        agent_provider: str,
    ) -> bool:
        for calibration_round in calibration_store.list_rounds():
            if calibration_round.status not in {"pending", "in_progress"}:
                continue
            metadata = calibration_round.metadata
            if (
                metadata.get("release", "") == release
                and metadata.get("scenario_family", "") == scenario_family
                and metadata.get("agent_provider", "") == agent_provider
            ):
                return True
        return False

    def _generate_run_trace_artifacts(
        self,
        run_id: str,
        scenario_name: str,
        scenario: ScenarioInterface,
    ) -> None:
        """Persist a canonical run trace plus inspector-facing summary artifacts."""
        generation_rows = self.sqlite.get_generation_metrics(run_id)
        role_metrics = self.sqlite.get_agent_role_metrics(run_id)
        staged_validations = self.sqlite.get_staged_validation_results_for_run(run_id)
        consultations = self.sqlite.get_consultations_for_run(run_id)
        recovery_markers = self.sqlite.get_recovery_markers_for_run(run_id)

        trace = self._build_run_trace(
            run_id=run_id,
            scenario_name=scenario_name,
            scenario=scenario,
            generation_rows=generation_rows,
            role_metrics=role_metrics,
            staged_validations=staged_validations,
            consultations=consultations,
            recovery_markers=recovery_markers,
        )
        analytics_root = self.settings.knowledge_root / "analytics"
        analytics_root.mkdir(parents=True, exist_ok=True)
        trace_path = TraceStore(analytics_root).persist(trace)
        self._persist_run_inspection(trace, analytics_root, trace_path)

    def _build_run_trace(
        self,
        *,
        run_id: str,
        scenario_name: str,
        scenario: ScenarioInterface,
        generation_rows: list[dict[str, Any]] | list,  # accepts GenerationMetricsRow too
        role_metrics: list[dict[str, Any]],
        staged_validations: list[dict[str, Any]],
        consultations: list[dict[str, Any]],
        recovery_markers: list[dict[str, Any]],
    ) -> RunTrace:
        """Build a canonical per-run trace from persisted runtime artifacts."""
        family = detect_family(scenario)
        generation_map = {
            self._int_value(row.get("generation_index"))
            : row
            for row in generation_rows
        }
        roles_by_generation: dict[int, list[dict[str, Any]]] = defaultdict(list)
        validations_by_generation: dict[int, list[dict[str, Any]]] = defaultdict(list)
        consultations_by_generation: dict[int, list[dict[str, Any]]] = defaultdict(list)
        recovery_by_generation: dict[int, list[dict[str, Any]]] = defaultdict(list)

        for row in role_metrics:
            roles_by_generation[self._int_value(row.get("generation_index"))].append(row)
        for row in staged_validations:
            validations_by_generation[self._int_value(row.get("generation_index"))].append(row)
        for row in consultations:
            consultations_by_generation[self._int_value(row.get("generation_index"))].append(row)
        for row in recovery_markers:
            recovery_by_generation[self._int_value(row.get("generation_index"))].append(row)

        generation_indices = sorted({
            *generation_map.keys(),
            *roles_by_generation.keys(),
            *validations_by_generation.keys(),
            *consultations_by_generation.keys(),
            *recovery_by_generation.keys(),
        })

        sequence_number = 1
        events: list[TraceEvent] = []
        causal_edges: list[CausalEdge] = []
        previous_checkpoint_id: str | None = None

        for generation_index in generation_indices:
            generation_row = generation_map.get(generation_index, {})
            current_source_id = previous_checkpoint_id
            failed_validation_ids: list[str] = []

            for metric_index, metric in enumerate(roles_by_generation.get(generation_index, []), start=1):
                event_id = f"gen-{generation_index}-role-{metric_index}"
                event = TraceEvent(
                    event_id=event_id,
                    run_id=run_id,
                    generation_index=generation_index,
                    sequence_number=sequence_number,
                    timestamp=str(metric.get("created_at", "")),
                    category="action",
                    event_type=f"{metric.get('role', 'agent')}_execution",
                    actor=ActorRef(
                        actor_type="role",
                        actor_id=str(metric.get("role", "agent")),
                        actor_name=str(metric.get("subagent_id") or metric.get("role", "agent")),
                    ),
                    resources=[
                        ResourceRef(
                            resource_type="model",
                            resource_id=str(metric.get("model", "")),
                            resource_name=str(metric.get("model", "")),
                            resource_path="",
                        )
                    ] if metric.get("model") else [],
                    summary=(
                        f"{metric.get('role', 'agent')} executed with "
                        f"{metric.get('status', 'unknown')} status"
                    ),
                    detail={
                        "model": metric.get("model"),
                        "input_tokens": metric.get("input_tokens"),
                        "output_tokens": metric.get("output_tokens"),
                        "latency_ms": metric.get("latency_ms"),
                        "subagent_id": metric.get("subagent_id"),
                        "status": metric.get("status"),
                    },
                    parent_event_id=current_source_id,
                    cause_event_ids=[current_source_id] if current_source_id else [],
                    evidence_ids=[],
                    severity="error" if metric.get("status") == "failed" else "info",
                    stage=self._role_stage(str(metric.get("role", ""))),
                    outcome=str(metric.get("status", "")),
                    duration_ms=self._int_value(metric.get("latency_ms")),
                    metadata={"scenario": scenario_name},
                )
                events.append(event)
                if current_source_id:
                    causal_edges.append(
                        CausalEdge(
                            source_event_id=current_source_id,
                            target_event_id=event_id,
                            relation="triggers",
                        )
                    )
                current_source_id = event_id
                sequence_number += 1

            for validation_index, validation in enumerate(validations_by_generation.get(generation_index, []), start=1):
                event_id = f"gen-{generation_index}-validation-{validation_index}"
                status = str(validation.get("status", "unknown"))
                event = TraceEvent(
                    event_id=event_id,
                    run_id=run_id,
                    generation_index=generation_index,
                    sequence_number=sequence_number,
                    timestamp=str(validation.get("created_at", "")),
                    category="validation",
                    event_type=f"validation_{validation.get('stage_name', 'unknown')}",
                    actor=ActorRef(
                        actor_type="system",
                        actor_id="validator",
                        actor_name="Validator",
                    ),
                    resources=[],
                    summary=(
                        f"Validation stage {validation.get('stage_name', 'unknown')} "
                        f"finished with {status}"
                    ),
                    detail={
                        "stage_name": validation.get("stage_name"),
                        "stage_order": validation.get("stage_order"),
                        "status": status,
                        "error": validation.get("error"),
                        "error_code": validation.get("error_code"),
                    },
                    parent_event_id=current_source_id,
                    cause_event_ids=[current_source_id] if current_source_id else [],
                    evidence_ids=[],
                    severity="error" if status == "failed" else "info",
                    stage="gate",
                    outcome=status,
                    duration_ms=self._int_value(validation.get("duration_ms")),
                    metadata={"scenario": scenario_name},
                )
                events.append(event)
                if current_source_id:
                    causal_edges.append(
                        CausalEdge(
                            source_event_id=current_source_id,
                            target_event_id=event_id,
                            relation="triggers",
                        )
                    )
                if status == "failed":
                    failed_validation_ids.append(event_id)
                current_source_id = event_id
                sequence_number += 1

            for consultation_index, consultation in enumerate(consultations_by_generation.get(generation_index, []), start=1):
                event_id = f"gen-{generation_index}-consultation-{consultation_index}"
                event = TraceEvent(
                    event_id=event_id,
                    run_id=run_id,
                    generation_index=generation_index,
                    sequence_number=sequence_number,
                    timestamp=str(consultation.get("created_at", "")),
                    category="observation",
                    event_type="consultation",
                    actor=ActorRef(
                        actor_type="external",
                        actor_id="consultation_provider",
                        actor_name=str(consultation.get("model_used", "consultation")),
                    ),
                    resources=[
                        ResourceRef(
                            resource_type="model",
                            resource_id=str(consultation.get("model_used", "")),
                            resource_name=str(consultation.get("model_used", "")),
                            resource_path="",
                        )
                    ] if consultation.get("model_used") else [],
                    summary=f"Consultation triggered by {consultation.get('trigger', 'unknown')}",
                    detail={
                        "trigger": consultation.get("trigger"),
                        "critique": consultation.get("critique"),
                        "alternative_hypothesis": consultation.get("alternative_hypothesis"),
                        "tiebreak_recommendation": consultation.get("tiebreak_recommendation"),
                        "suggested_next_action": consultation.get("suggested_next_action"),
                        "cost_usd": consultation.get("cost_usd"),
                    },
                    parent_event_id=current_source_id,
                    cause_event_ids=[current_source_id] if current_source_id else [],
                    evidence_ids=[],
                    severity="warning",
                    stage="analyze",
                    outcome="advisory",
                    duration_ms=None,
                    metadata={"scenario": scenario_name},
                )
                events.append(event)
                if current_source_id:
                    causal_edges.append(
                        CausalEdge(
                            source_event_id=current_source_id,
                            target_event_id=event_id,
                            relation="triggers",
                        )
                    )
                current_source_id = event_id
                sequence_number += 1

            for recovery_index, marker in enumerate(recovery_by_generation.get(generation_index, []), start=1):
                event_id = f"gen-{generation_index}-recovery-{recovery_index}"
                cause_ids = list(failed_validation_ids)
                if current_source_id and current_source_id not in cause_ids:
                    cause_ids.append(current_source_id)
                event = TraceEvent(
                    event_id=event_id,
                    run_id=run_id,
                    generation_index=generation_index,
                    sequence_number=sequence_number,
                    timestamp=str(marker.get("created_at", "")),
                    category="recovery",
                    event_type=f"recovery_{marker.get('decision', 'unknown')}",
                    actor=ActorRef(
                        actor_type="system",
                        actor_id="recovery_manager",
                        actor_name="Recovery Manager",
                    ),
                    resources=[],
                    summary=f"Recovery decision {marker.get('decision', 'unknown')}",
                    detail={
                        "decision": marker.get("decision"),
                        "reason": marker.get("reason"),
                        "retry_count": marker.get("retry_count"),
                    },
                    parent_event_id=current_source_id,
                    cause_event_ids=cause_ids,
                    evidence_ids=failed_validation_ids,
                    severity="warning",
                    stage="gate",
                    outcome=str(marker.get("decision", "")),
                    duration_ms=None,
                    metadata={"scenario": scenario_name},
                )
                events.append(event)
                if current_source_id and current_source_id not in failed_validation_ids:
                    causal_edges.append(
                        CausalEdge(
                            source_event_id=current_source_id,
                            target_event_id=event_id,
                            relation="triggers",
                        )
                    )
                for failed_validation_id in failed_validation_ids:
                    causal_edges.append(
                        CausalEdge(
                            source_event_id=failed_validation_id,
                            target_event_id=event_id,
                            relation="recovers",
                        )
                    )
                current_source_id = event_id
                sequence_number += 1

            checkpoint_id = f"gen-{generation_index}-checkpoint"
            gate_decision = str(generation_row.get("gate_decision", "unknown"))
            checkpoint_causes = [current_source_id] if current_source_id else []
            checkpoint = TraceEvent(
                event_id=checkpoint_id,
                run_id=run_id,
                generation_index=generation_index,
                sequence_number=sequence_number,
                timestamp=str(generation_row.get("updated_at") or generation_row.get("created_at", "")),
                category="checkpoint",
                event_type="generation_summary",
                actor=ActorRef(
                    actor_type="system",
                    actor_id="generation_runner",
                    actor_name="Generation Runner",
                ),
                resources=[
                    ResourceRef(
                        resource_type="scenario_entity",
                        resource_id=f"generation:{generation_index}",
                        resource_name=f"Generation {generation_index}",
                        resource_path="",
                    )
                ],
                summary=f"Generation {generation_index} completed with {gate_decision}",
                detail={
                    "mean_score": generation_row.get("mean_score"),
                    "best_score": generation_row.get("best_score"),
                    "elo": generation_row.get("elo"),
                    "wins": generation_row.get("wins"),
                    "losses": generation_row.get("losses"),
                    "status": generation_row.get("status"),
                    "gate_decision": generation_row.get("gate_decision"),
                },
                parent_event_id=current_source_id,
                cause_event_ids=checkpoint_causes,
                evidence_ids=failed_validation_ids,
                severity="error" if gate_decision == "error" else "info",
                stage="gate",
                outcome=gate_decision,
                duration_ms=self._duration_to_ms(generation_row.get("duration_seconds")),
                metadata={"scenario": scenario_name},
            )
            events.append(checkpoint)
            if current_source_id:
                causal_edges.append(
                    CausalEdge(
                        source_event_id=current_source_id,
                        target_event_id=checkpoint_id,
                        relation="depends_on",
                    )
                )
            previous_checkpoint_id = checkpoint_id
            sequence_number += 1

        created_at = ""
        if events:
            created_at = events[0].timestamp
        elif generation_rows:
            created_at = str(generation_rows[0].get("created_at", ""))

        return RunTrace(
            trace_id=f"trace-{run_id}",
            run_id=run_id,
            generation_index=None,
            schema_version="1.0.0",
            events=events,
            causal_edges=causal_edges,
            created_at=created_at,
            metadata={
                "scenario": scenario_name,
                "scenario_family": family.name if family is not None else "",
                "agent_provider": self.settings.agent_provider,
                "executor_mode": self.settings.executor_mode,
                "release": _current_release_version(),
                "total_generations": len(generation_indices),
            },
        )

    def _persist_run_inspection(
        self,
        trace: RunTrace,
        analytics_root: Path,
        trace_path: Path,
    ) -> None:
        """Persist operator-facing inspection artifacts derived from a run trace."""
        inspection_dir = analytics_root / "inspections"
        inspection_dir.mkdir(parents=True, exist_ok=True)
        inspector = StateInspector()
        builder = TimelineBuilder()
        generation_indices = sorted({
            event.generation_index for event in trace.events if event.generation_index is not None
        })
        payload = {
            "trace_id": trace.trace_id,
            "run_id": trace.run_id,
            "trace_path": str(trace_path),
            "created_at": trace.created_at,
            "run_inspection": inspector.inspect_run(trace).model_dump(),
            "generation_inspections": [
                inspector.inspect_generation(trace, generation_index).model_dump()
                for generation_index in generation_indices
            ],
            "timeline_summary": [entry.to_dict() for entry in builder.build_summary(trace)],
            "failure_paths": [
                [event.event_id for event in path]
                for path in inspector.find_failure_paths(trace)
            ],
            "recovery_paths": [
                [event.event_id for event in path]
                for path in inspector.find_recovery_paths(trace)
            ],
        }
        (inspection_dir / f"{trace.trace_id}.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def _role_stage(self, role: str) -> str:
        return {
            "competitor": "compete",
            "analyst": "analyze",
            "coach": "coach",
            "architect": "architect",
            "curator": "curate",
        }.get(role, "analyze")

    def _duration_to_ms(self, duration_seconds: object) -> int | None:
        if duration_seconds is None:
            return None
        try:
            return int(self._float_value(duration_seconds) * 1000)
        except (TypeError, ValueError):
            return None

    def _int_value(self, value: object, default: int = 0) -> int:
        try:
            return int(cast(int | float | str, value))
        except (TypeError, ValueError):
            return default

    def _float_value(self, value: object, default: float = 0.0) -> float:
        try:
            return float(cast(int | float | str, value))
        except (TypeError, ValueError):
            return default

    def _optional_float_value(
        self,
        value: object,
        default: float | None = None,
    ) -> float | None:
        if value is None:
            return default
        try:
            return float(cast(int | float | str, value))
        except (TypeError, ValueError):
            return default

    def _recover_stale_run_state(self, run_id: str) -> None:
        """Repair an interrupted run before attempting a resume.

        This handles the persisted broken state left behind after a prior process
        died or was interrupted while a run still had `running` rows in SQLite.
        """
        run_row = self.sqlite.get_run(run_id)
        if run_row is None or str(run_row.get("status") or "") != "running":
            return

        generation_rows = self.sqlite.get_generation_metrics(run_id)
        running_generations = [
            self._int_value(row.get("generation_index"))
            for row in generation_rows
            if str(row.get("status") or "") == "running"
        ]
        if running_generations:
            recovery_markers = self.sqlite.get_recovery_markers_for_run(run_id)
            retry_counts: dict[int, int] = defaultdict(int)
            for marker in recovery_markers:
                retry_counts[self._int_value(marker.get("generation_index"))] += 1
            for generation_index in running_generations:
                self.sqlite.update_generation_status(
                    run_id,
                    generation_index,
                    status="failed",
                    gate_decision="stalled",
                )
                self.sqlite.append_recovery_marker(
                    run_id,
                    generation_index,
                    decision="mark_failed",
                    reason="Recovered stale running generation from a prior interrupted run",
                    retry_count=retry_counts[generation_index] + 1,
                )
            self.sqlite.mark_run_failed(run_id)
            logger.warning(
                "recovered stale running generations for run %s: %s",
                run_id,
                ", ".join(str(gen) for gen in running_generations),
            )
            return

        completed_generations = sum(
            1 for row in generation_rows if str(row.get("status") or "") == "completed"
        )
        target_generations = self._int_value(run_row.get("target_generations"), 0)
        if target_generations > 0 and completed_generations >= target_generations:
            self.sqlite.mark_run_completed(run_id)
            logger.info(
                "marking run %s completed during recovery (%d/%d generations already completed)",
                run_id,
                completed_generations,
                target_generations,
            )
            return

        self.sqlite.mark_run_failed(run_id)
        logger.warning(
            "marking run %s failed during recovery; run was still 'running' without an active generation",
            run_id,
        )

    def _hydrate_run_state(
        self,
        run_id: str,
    ) -> tuple[float, float, float | None, list[float], list[str]]:
        """Restore prior completed-generation state for resume/retry flows."""
        default_uncertainty = self._optional_float_value(
            get_backend(self.settings.scoring_backend).default_uncertainty,
        )
        generation_rows = self.sqlite.get_generation_metrics(run_id)
        completed_rows = [
            row for row in generation_rows if str(row.get("status") or "") == "completed"
        ]
        if not completed_rows:
            return 0.0, 1000.0, default_uncertainty, [], []

        latest = completed_rows[-1]
        previous_best = self._float_value(latest.get("best_score"), 0.0)
        challenger_elo = self._float_value(latest.get("elo"), 1000.0)
        challenger_uncertainty = self._optional_float_value(
            latest.get("rating_uncertainty"),
            default_uncertainty,
        )
        score_history = [
            self._float_value(row.get("best_score"), 0.0) for row in completed_rows
        ]
        gate_decision_history = [
            str(row.get("gate_decision") or "") for row in completed_rows if row.get("gate_decision")
        ]
        return (
            previous_best,
            challenger_elo,
            challenger_uncertainty,
            score_history,
            gate_decision_history,
        )

    def run(self, scenario_name: str, generations: int, run_id: str | None = None) -> RunSummary:
        scenario = self._scenario(scenario_name)
        active_run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        run_start_time = time.monotonic()
        existing_run = self.sqlite.get_run(active_run_id)
        if existing_run is None:
            self.sqlite.create_run(
                active_run_id, scenario_name, generations, self.settings.executor_mode,
                agent_provider=self.settings.agent_provider,
            )
        else:
            self._recover_stale_run_state(active_run_id)
            refreshed_run = self.sqlite.get_run(active_run_id) or existing_run
            if str(refreshed_run.get("status") or "") != "completed":
                target_generations = max(
                    self._int_value(refreshed_run.get("target_generations"), generations),
                    generations,
                )
                self.sqlite.mark_run_running(active_run_id, target_generations=target_generations)
        (
            previous_best,
            challenger_elo,
            challenger_uncertainty,
            score_history,
            gate_decision_history,
        ) = self._hydrate_run_state(active_run_id)
        completed = 0
        self.events.emit("run_started", {"run_id": active_run_id, "scenario": scenario_name})

        # Seed scenario-specific tools before first generation
        if not self.artifacts.tools_dir(scenario_name).exists():
            seed = scenario.seed_tools()
            if seed:
                seed_tool_list: list[dict[str, Any]] = [
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
                        logger.info(
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
                logger.info(
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
                existing_generation = self.sqlite.get_generation(active_run_id, generation)
                if existing_generation is not None:
                    status = str(existing_generation.get("status") or "")
                    if status == "completed":
                        logger.info(
                            "generation %s already completed for run %s, skipping for idempotency",
                            generation,
                            active_run_id,
                        )
                        continue
                    logger.warning(
                        "generation %s for run %s exists with status '%s'; rerunning generation",
                        generation,
                        active_run_id,
                        status or "unknown",
                    )
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
                    scoring_backend=self.settings.scoring_backend,
                    rating_uncertainty=challenger_uncertainty,
                )
                try:
                    from autocontext.loop.generation_pipeline import GenerationPipeline
                    from autocontext.loop.stage_types import GenerationContext

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
                        challenger_uncertainty=challenger_uncertainty,
                        score_history=score_history,
                        gate_decision_history=gate_decision_history,
                        coach_competitor_hints=coach_competitor_hints,
                        replay_narrative=replay_narrative,
                    )
                    ctx = pipeline.run_generation(ctx)
                    self.artifacts.mutation_log.append(
                        scenario_name,
                        MutationEntry(
                            mutation_type="run_outcome",
                            generation=generation,
                            payload={
                                "gate_decision": ctx.gate_decision,
                                "best_score": ctx.previous_best,
                                "elo": ctx.challenger_elo,
                                "scoring_backend": ctx.settings.scoring_backend,
                                "rating_uncertainty": ctx.challenger_uncertainty,
                            },
                            run_id=active_run_id,
                            description=f"Generation {generation} completed with {ctx.gate_decision or 'unknown'}",
                        ),
                    )
                    previous_best = ctx.previous_best
                    challenger_elo = ctx.challenger_elo
                    challenger_uncertainty = self._optional_float_value(
                        ctx.challenger_uncertainty,
                        challenger_uncertainty,
                    )
                    replay_narrative = ctx.replay_narrative
                    coach_competitor_hints = ctx.coach_competitor_hints
                    completed += 1
                except Exception as exc:
                    logger.debug("loop.generation_runner: caught Exception", exc_info=True)
                    self.artifacts.mutation_log.append(
                        scenario_name,
                        MutationEntry(
                            mutation_type="run_outcome",
                            generation=generation,
                            payload={"status": "failed", "error": str(exc)},
                            run_id=active_run_id,
                            description=f"Generation {generation} failed",
                        ),
                    )
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
                        scoring_backend=self.settings.scoring_backend,
                        rating_uncertainty=challenger_uncertainty,
                    )
                    self.events.emit(
                        "generation_failed",
                        {"run_id": active_run_id, "generation": generation, "error": str(exc)},
                    )
                    raise
                finally:
                    # Post-unwind safety net: if the pipeline exited without
                    # persisting a terminal status, mark the generation failed.
                    try:
                        gen_row = self.sqlite.get_generation(active_run_id, generation)
                        if gen_row and gen_row.get("status") == "running":
                            logger.warning(
                                "generation %d for run %s still in 'running' state after pipeline exit; marking as stalled",
                                generation, active_run_id,
                            )
                            self.sqlite.update_generation_status(
                                active_run_id,
                                generation,
                                status="failed",
                                gate_decision="stalled",
                            )
                    except Exception:
                        logger.debug("loop.generation_runner: suppressed Exception", exc_info=True)
            self.sqlite.mark_run_completed(active_run_id)
            if completed > 0:
                self.artifacts.mutation_log.create_checkpoint(
                    scenario_name,
                    generation=completed,
                    run_id=active_run_id,
                )
            self.artifacts.flush_writes()
        except BaseException:
            try:
                run_row = self.sqlite.get_run(active_run_id)
                if run_row is not None and str(run_row.get("status") or "") == "running":
                    self._recover_stale_run_state(active_run_id)
            except Exception:
                logger.warning("failed to recover stale run state for %s", active_run_id, exc_info=True)
            raise
        finally:
            self.artifacts.shutdown_writer()

        # Generate session report
        if self.settings.session_reports_enabled:
            duration = time.monotonic() - run_start_time
            try:
                self._generate_session_report(active_run_id, scenario_name, duration)
            except Exception:
                logger.warning("failed to generate session report for run %s", active_run_id, exc_info=True)
        try:
            self._generate_progress_report(active_run_id, scenario_name)
        except Exception:
            logger.warning("failed to generate progress report for run %s", active_run_id, exc_info=True)
        try:
            self._generate_aggregate_analytics(active_run_id, scenario_name, scenario)
        except Exception:
            logger.warning("failed to generate aggregate analytics for run %s", active_run_id, exc_info=True)
        try:
            self._generate_run_trace_artifacts(active_run_id, scenario_name, scenario)
        except Exception:
            logger.warning("failed to generate run trace artifacts for run %s", active_run_id, exc_info=True)
        try:
            self._generate_trace_grounded_reports(active_run_id, scenario_name)
        except Exception:
            logger.warning("failed to generate trace-grounded reports for run %s", active_run_id, exc_info=True)

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
                scoring_backend=self.settings.scoring_backend,
                rating_uncertainty=challenger_uncertainty,
            )

        self.events.emit("run_completed", {"run_id": active_run_id, "completed_generations": completed})
        return RunSummary(
            run_id=active_run_id,
            scenario=scenario_name,
            generations_executed=completed,
            best_score=previous_best,
            current_elo=challenger_elo,
        )
