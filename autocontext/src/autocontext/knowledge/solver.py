"""Solve-on-demand — background scenario creation and strategy evolution."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from autocontext.agents.types import LlmFn
from autocontext.cli_role_runtime import resolve_role_runtime
from autocontext.config.settings import AppSettings
from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.knowledge.export import SkillPackage, export_skill_package
from autocontext.mcp.tools import MtsToolContext
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.scenarios.artifact_editing import Artifact, ArtifactEditingInterface
from autocontext.storage import artifact_store_from_settings
from autocontext.storage.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from autocontext.scenarios.families import ScenarioFamily

logger = logging.getLogger(__name__)


class _NamedScenario(Protocol):
    name: str


_FAMILY_HEADER_RE = re.compile(r"^\s*\*{0,2}family\*{0,2}:\s*(?P<body>.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_SOLVE_DESCRIPTION_SKIP_SECTIONS = frozenset(
    {
        "Why This Matters",
        "What This Tests",
        "Implementation Guidance",
        "Acceptance",
        "Why existing scenarios don't cover this",
        "Dependencies",
    }
)
_SOLVE_DESCRIPTION_SKIP_LINE_PREFIXES = (
    "**Priority:**",
    "**Generations to signal:**",
)
_SOLVE_FAMILY_ALIASES = {
    "alignment_stress_test": "agent_task",
    "meta_learning": "agent_task",
    "capability_bootstrapping": "agent_task",
}
_SIMULATION_INTERFACE_HINT_RE = re.compile(
    r"\bsimulationinterface\b.*\bworldstate\b|\bworldstate\b.*\bsimulationinterface\b",
    re.IGNORECASE | re.DOTALL,
)
_AGENT_TASK_INTERFACE_HINT_RE = re.compile(r"\bagent[- ]task evaluation\b", re.IGNORECASE)
_SOLVE_INLINE_EXAMPLE_PAREN_RE = re.compile(
    r"\(\s*(?:e\.g\.,?|eg,?|for example,?)[^)]*\)",
    re.IGNORECASE,
)


@dataclass
class SolveJob:
    job_id: str
    description: str
    scenario_name: str | None = None
    status: str = "pending"
    generations: int = 5
    progress: int = 0
    error: str | None = None
    result: SkillPackage | None = None
    created_at: float = field(default_factory=time.time)
    family_override: str | None = None


@dataclass(slots=True)
class SolveScenarioBuildResult:
    scenario_name: str
    family_name: str


@dataclass(slots=True)
class SolveExecutionSummary:
    run_id: str
    generations_executed: int
    best_score: float


class ArtifactEditingTaskAdapter(AgentTaskInterface):
    """Adapt artifact-editing scenarios onto the task-bearing execution loop."""

    def __init__(self, scenario: ArtifactEditingInterface) -> None:
        self._scenario = scenario
        self.name = getattr(scenario, "name", scenario.__class__.__name__)

    def describe_task(self) -> str:
        return self._scenario.describe_task()

    def get_rubric(self) -> str:
        return self._scenario.get_rubric()

    def initial_state(self, seed: int | None = None) -> dict:
        return {
            "original_artifacts": [artifact.to_dict() for artifact in self._scenario.initial_artifacts(seed)],
        }

    def get_task_prompt(self, state: dict) -> str:
        return self._scenario.get_edit_prompt(self._original_artifacts(state))

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        del reference_context, required_concepts, calibration_examples, pinned_dimensions
        original = self._original_artifacts(state)
        try:
            edited = self._parse_edited_artifacts(output, original)
        except Exception as exc:
            return AgentTaskResult(
                score=0.0,
                reasoning=f"Edited artifact JSON parse failed: {exc}",
                dimension_scores={},
            )

        result = self._scenario.evaluate_edits(original, edited)
        reasoning = result.reasoning
        if result.validation.errors:
            reasoning = f"{reasoning} Validation errors: {'; '.join(result.validation.errors)}"
        return AgentTaskResult(
            score=result.score,
            reasoning=reasoning,
            dimension_scores=result.dimension_scores,
        )

    def _original_artifacts(self, state: dict) -> list[Artifact]:
        payload = state.get("original_artifacts")
        if isinstance(payload, list):
            try:
                return [Artifact.from_dict(cast(dict[str, Any], item)) for item in payload]
            except Exception:
                logger.debug("failed to restore original artifacts from state", exc_info=True)
        return self._scenario.initial_artifacts()

    def _parse_edited_artifacts(self, output: str, original: list[Artifact]) -> list[Artifact]:
        text = output.strip()
        json_start = text.find("{")
        json_end = text.rfind("}")
        if json_start == -1 or json_end == -1 or json_end <= json_start:
            raise ValueError("output does not contain an edited-artifact JSON object")
        payload = json.loads(text[json_start : json_end + 1])
        artifact_payloads = payload.get("artifacts") if isinstance(payload, dict) else None
        if not isinstance(artifact_payloads, list):
            raise ValueError("output JSON must contain an 'artifacts' list")

        original_by_path = {artifact.path: artifact for artifact in original}
        edited_by_path: dict[str, Artifact] = {}
        for item in artifact_payloads:
            if not isinstance(item, dict):
                raise ValueError("edited artifacts must be objects")
            path = str(item.get("path", "")).strip()
            content = item.get("content")
            if not path or not isinstance(content, str):
                raise ValueError("each edited artifact must include string path and content fields")
            original_artifact = original_by_path.get(path)
            content_type = item.get("content_type")
            metadata = item.get("metadata")
            edited_by_path[path] = Artifact(
                path=path,
                content=content,
                content_type=(
                    str(content_type)
                    if isinstance(content_type, str) and content_type.strip()
                    else (original_artifact.content_type if original_artifact is not None else "text")
                ),
                metadata=(
                    cast(dict[str, Any], metadata)
                    if isinstance(metadata, dict)
                    else (original_artifact.metadata if original_artifact is not None else {})
                ),
            )
        return list(edited_by_path.values())


@dataclass(slots=True)
class _SolveGenerationBudget:
    scenario_name: str
    budget_seconds: int
    started_at: float = field(default_factory=lambda: time.monotonic())

    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def check(self, phase: str) -> None:
        if self.budget_seconds <= 0:
            return
        elapsed = self.elapsed_seconds()
        if elapsed >= self.budget_seconds:
            raise TimeoutError(
                f"Solve generation time budget exceeded during {phase} "
                f"after {elapsed:.2f}s for scenario '{self.scenario_name}' "
                f"(budget {self.budget_seconds}s)"
            )


class _BudgetedAgentTask(AgentTaskInterface):
    """Add solve generation budget checks around an AgentTaskInterface."""

    def __init__(self, task: AgentTaskInterface, budget: _SolveGenerationBudget) -> None:
        self._task = task
        self._budget = budget
        self.name = getattr(task, "name", task.__class__.__name__)

    def get_task_prompt(self, state: dict) -> str:
        self._budget.check("task prompt")
        prompt = self._task.get_task_prompt(state)
        self._budget.check("task prompt")
        return prompt

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        self._budget.check("evaluation")
        result = self._task.evaluate_output(
            output,
            state,
            reference_context=reference_context,
            required_concepts=required_concepts,
            calibration_examples=calibration_examples,
            pinned_dimensions=pinned_dimensions,
        )
        self._budget.check("evaluation")
        return result

    def get_rubric(self) -> str:
        self._budget.check("rubric")
        rubric = self._task.get_rubric()
        self._budget.check("rubric")
        return rubric

    def initial_state(self, seed: int | None = None) -> dict:
        self._budget.check("initial state")
        state = self._task.initial_state(seed)
        self._budget.check("initial state")
        return state

    def describe_task(self) -> str:
        self._budget.check("task description")
        description = self._task.describe_task()
        self._budget.check("task description")
        return description

    def prepare_context(self, state: dict) -> dict:
        self._budget.check("context preparation")
        prepared = self._task.prepare_context(state)
        self._budget.check("context preparation")
        return prepared

    def validate_context(self, state: dict) -> list[str]:
        self._budget.check("context validation")
        errors = self._task.validate_context(state)
        self._budget.check("context validation")
        return errors

    def revise_output(
        self,
        output: str,
        judge_result: AgentTaskResult,
        state: dict,
    ) -> str:
        self._budget.check("revision")
        revised = self._task.revise_output(output, judge_result, state)
        self._budget.check("revision")
        return revised

    def verify_facts(self, output: str, state: dict) -> dict | None:
        self._budget.check("fact verification")
        result = self._task.verify_facts(output, state)
        self._budget.check("fact verification")
        return result


def _build_solve_description_brief(description: str) -> str:
    lines: list[str] = []
    skipping_section = False
    for raw_line in description.splitlines():
        heading_match = re.match(r"^\s*#{2,6}\s+(.+?)\s*$", raw_line)
        if heading_match is not None:
            title = heading_match.group(1).strip()
            skipping_section = title in _SOLVE_DESCRIPTION_SKIP_SECTIONS
            if not skipping_section:
                lines.append(raw_line)
            continue

        stripped = raw_line.strip()
        if stripped.startswith(_SOLVE_DESCRIPTION_SKIP_LINE_PREFIXES):
            continue
        if not skipping_section:
            lines.append(raw_line)

    brief = "\n".join(lines).strip()
    brief = _SOLVE_INLINE_EXAMPLE_PAREN_RE.sub("", brief)
    brief = re.sub(r"\n{3,}", "\n\n", brief)
    brief = re.sub(r"[ \t]{2,}", " ", brief)
    return brief or description.strip()


def _normalize_family_hint_token(token: str) -> str:
    normalized = re.sub(r"[^a-z0-9_\-\s]", " ", token.lower()).strip()
    return normalized.replace("-", "_").replace(" ", "_")


def _resolve_family_hint(description: str) -> ScenarioFamily | None:
    from autocontext.scenarios.families import get_family, list_families

    match = _FAMILY_HEADER_RE.search(description)
    if match is None:
        return None

    supported = {family.name: family for family in list_families()}
    raw_hint = match.group("body")
    for token in re.split(r"[/,|]", raw_hint):
        candidate = _normalize_family_hint_token(token)
        if candidate in supported:
            return get_family(candidate)
    return None


def _resolve_solve_family_alias(description: str) -> ScenarioFamily | None:
    from autocontext.scenarios.families import get_family

    match = _FAMILY_HEADER_RE.search(description)
    if match is not None:
        for token in re.split(r"[/,|]", match.group("body")):
            candidate = _normalize_family_hint_token(token)
            aliased = _SOLVE_FAMILY_ALIASES.get(candidate)
            if aliased is not None:
                return get_family(aliased)

    if _SIMULATION_INTERFACE_HINT_RE.search(description):
        return get_family("simulation")
    if _AGENT_TASK_INTERFACE_HINT_RE.search(description):
        return get_family("agent_task")
    return None


def _resolve_requested_scenario_family(
    description: str,
    *,
    llm_fn: LlmFn | None = None,
) -> ScenarioFamily:
    from autocontext.scenarios.custom.family_classifier import classify_scenario_family, route_to_family

    brief = _build_solve_description_brief(description)
    hinted_family = _resolve_family_hint(brief)
    if hinted_family is not None:
        return hinted_family

    aliased_family = _resolve_solve_family_alias(brief)
    if aliased_family is not None:
        return aliased_family

    classification = classify_scenario_family(brief, llm_fn=llm_fn)
    return route_to_family(classification)


class SolveScenarioExecutor:
    """Execute created solve scenarios through the correct family-aware runtime surface."""

    def __init__(self, settings: AppSettings, *, migrations_dir: Path | None = None) -> None:
        self._settings = settings
        self._migrations_dir = migrations_dir or Path(__file__).resolve().parents[2] / "migrations"

    def execute(
        self,
        *,
        scenario_name: str,
        family_name: str,
        generations: int,
    ) -> SolveExecutionSummary:
        scenario = self._scenario(scenario_name)
        if isinstance(scenario, AgentTaskInterface):
            return self._run_task_like_scenario(
                scenario_name=scenario_name,
                scenario_type="agent_task",
                task=scenario,
                max_rounds=generations,
            )
        if isinstance(scenario, ArtifactEditingInterface):
            return self._run_task_like_scenario(
                scenario_name=scenario_name,
                scenario_type="artifact_editing",
                task=ArtifactEditingTaskAdapter(scenario),
                max_rounds=generations,
            )
        if family_name in {"agent_task", "artifact_editing"}:
            raise TypeError(
                f"Solve created family '{family_name}' for scenario '{scenario_name}', "
                "but the generated class does not expose the expected execution interface"
            )

        from autocontext.loop.generation_runner import GenerationRunner

        runner = GenerationRunner(self._settings)
        runner.migrate(self._migrations_dir)
        run_id = f"solve_{scenario_name}_{uuid.uuid4().hex[:8]}"
        summary = runner.run(scenario_name, generations, run_id)
        return SolveExecutionSummary(
            run_id=summary.run_id,
            generations_executed=summary.generations_executed,
            best_score=summary.best_score,
        )

    def _scenario(self, scenario_name: str) -> Any:
        cls = SCENARIO_REGISTRY.get(scenario_name)
        if cls is None:
            from autocontext.scenarios.custom.registry import load_all_custom_scenarios

            custom = load_all_custom_scenarios(self._settings.knowledge_root)
            if custom:
                SCENARIO_REGISTRY.update(custom)
            cls = SCENARIO_REGISTRY.get(scenario_name)
        if cls is None:
            supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
            raise ValueError(f"Unknown scenario '{scenario_name}'. Supported: {supported}")
        return cls()

    def _run_task_like_scenario(
        self,
        *,
        scenario_name: str,
        scenario_type: str,
        task: AgentTaskInterface,
        max_rounds: int,
    ) -> SolveExecutionSummary:
        sqlite = SQLiteStore(self._settings.db_path)
        sqlite.migrate(self._migrations_dir)
        active_run_id = f"solve_{scenario_name}_{uuid.uuid4().hex[:8]}"
        sqlite.create_run(
            active_run_id,
            scenario_name,
            1,
            scenario_type,
            agent_provider=self._settings.agent_provider,
        )
        sqlite.upsert_generation(
            active_run_id,
            1,
            mean_score=0.0,
            best_score=0.0,
            elo=0.0,
            wins=0,
            losses=0,
            gate_decision="running",
            status="running",
        )
        budget = _SolveGenerationBudget(
            scenario_name=scenario_name,
            budget_seconds=self._settings.generation_time_budget_seconds,
        )

        try:
            provider, provider_model = resolve_role_runtime(
                self._settings,
                role="competitor",
                scenario_name=scenario_name,
                sqlite=sqlite,
            )
            budget.check("runtime resolution")
            budgeted_task = _BudgetedAgentTask(task, budget)
            state = budgeted_task.prepare_context(budgeted_task.initial_state())
            context_errors = budgeted_task.validate_context(state)
            if context_errors:
                raise ValueError(f"Context validation failed: {'; '.join(context_errors)}")
            prompt = budgeted_task.get_task_prompt(state)
            initial_output = provider.complete(
                system_prompt="Complete the task precisely.",
                user_prompt=prompt,
                model=provider_model,
            ).text
            budget.check("initial generation")
            sqlite.append_agent_output(active_run_id, 1, "competitor_initial", initial_output)

            loop = ImprovementLoop(task=budgeted_task, max_rounds=max_rounds)
            result = loop.run(initial_output=initial_output, state=state)
            budget.check("improvement loop")
        except Exception:
            sqlite.upsert_generation(
                active_run_id,
                1,
                mean_score=0.0,
                best_score=0.0,
                elo=0.0,
                wins=0,
                losses=0,
                gate_decision="failed",
                status="failed",
                duration_seconds=budget.elapsed_seconds(),
            )
            sqlite.mark_run_failed(active_run_id)
            raise

        sqlite.append_agent_output(active_run_id, 1, "competitor", result.best_output)
        sqlite.upsert_generation(
            active_run_id,
            1,
            mean_score=result.best_score,
            best_score=result.best_score,
            elo=0.0,
            wins=0,
            losses=0,
            gate_decision=result.termination_reason,
            status="completed",
            duration_seconds=(result.duration_ms / 1000.0) if result.duration_ms is not None else None,
        )
        sqlite.mark_run_completed(active_run_id)
        if self._settings.cross_run_inheritance and not self._settings.ablation_no_feedback:
            artifacts = artifact_store_from_settings(self._settings)
            playbook_hash = artifacts.snapshot_knowledge(scenario_name, active_run_id)
            sqlite.save_knowledge_snapshot(
                scenario=scenario_name,
                run_id=active_run_id,
                best_score=result.best_score,
                best_elo=1500.0,
                playbook_hash=playbook_hash,
                agent_provider=self._settings.agent_provider,
                rlm_enabled=self._settings.rlm_enabled,
                scoring_backend=self._settings.scoring_backend,
            )
        return SolveExecutionSummary(
            run_id=active_run_id,
            generations_executed=result.total_rounds,
            best_score=result.best_score,
        )


class SolveScenarioBuilder:
    """Create solve scenarios through the correct family-specific pipeline."""

    def __init__(
        self,
        *,
        runtime: Any,
        llm_fn: LlmFn,
        model: str,
        knowledge_root: Path,
    ) -> None:
        self._runtime = runtime
        self._llm_fn = llm_fn
        self._model = model
        self._knowledge_root = knowledge_root

    def build(
        self,
        description: str,
        *,
        family_override: str | None = None,
    ) -> SolveScenarioBuildResult:
        from autocontext.scenarios.custom.agent_task_creator import AgentTaskCreator
        from autocontext.scenarios.custom.creator import ScenarioCreator
        from autocontext.scenarios.families import get_family

        brief = _build_solve_description_brief(description)
        if family_override:
            family = get_family(family_override)
        else:
            family = _resolve_requested_scenario_family(brief, llm_fn=self._llm_fn)

        if family.name == "game":
            game_creator = ScenarioCreator(
                runtime=self._runtime,
                model=self._model,
                knowledge_root=self._knowledge_root,
            )
            spec = game_creator.generate_spec(brief)
            build = game_creator.build_and_validate(spec)
            SCENARIO_REGISTRY[spec.name] = build.scenario_class
            return SolveScenarioBuildResult(
                scenario_name=spec.name,
                family_name=family.name,
            )

        family_creator = AgentTaskCreator(
            llm_fn=self._llm_fn,
            knowledge_root=self._knowledge_root,
        )
        scenario = family_creator.create(brief, family_name=family.name)
        scenario_name = str(cast(_NamedScenario, scenario).name)
        SCENARIO_REGISTRY[scenario_name] = scenario.__class__
        return SolveScenarioBuildResult(
            scenario_name=scenario_name,
            family_name=family.name,
        )


def _llm_fn_from_client(client: Any, model: str) -> LlmFn:
    def llm_fn(system: str, user: str) -> str:
        response = client.generate(
            model=model,
            prompt=f"{system}\n\n{user}",
            max_tokens=1800,
            temperature=0.3,
            role="scenario_designer",
        )
        response_text: object = getattr(response, "text", "")
        if not isinstance(response_text, str):
            response_text = str(response_text)
        return response_text.strip()

    return llm_fn


class SolveManager:
    """Manage solve-on-demand jobs: create scenario -> run generations -> export skill."""

    def __init__(self, settings: AppSettings) -> None:
        self._jobs: dict[str, SolveJob] = {}
        self._settings = settings
        self._migrations_dir = Path(__file__).resolve().parents[2] / "migrations"

    def submit(self, description: str, generations: int = 5) -> str:
        """Create a solve job and run it in a background thread. Returns job_id."""
        job_id = f"solve_{uuid.uuid4().hex[:8]}"
        job = SolveJob(
            job_id=job_id,
            description=description,
            generations=generations,
        )
        self._jobs[job_id] = job
        thread = threading.Thread(target=self._run_job, args=(job,), daemon=True)
        thread.start()
        return job_id

    def solve_sync(
        self,
        description: str,
        generations: int = 5,
        family_override: str | None = None,
    ) -> SolveJob:
        """Run solve-on-demand synchronously in the current process.

        If ``family_override`` is provided, the scenario family classifier is
        bypassed and the solver routes directly to the named family's pipeline.
        """
        job_id = f"solve_{uuid.uuid4().hex[:8]}"
        job = SolveJob(
            job_id=job_id,
            description=description,
            generations=generations,
            family_override=family_override,
        )
        self._jobs[job_id] = job
        self._run_job(job)
        return job

    def _run_job(self, job: SolveJob) -> None:
        """Background: create scenario -> run generations -> export skill package."""
        try:
            # 1. Create scenario
            job.status = "creating_scenario"
            builder = self._build_creator()
            if builder is None:
                job.status = "failed"
                job.error = "Scenario creation pipeline unavailable (no API key or unsupported provider)"
                return

            created = builder.build(job.description, family_override=job.family_override)
            job.scenario_name = created.scenario_name

            # 2. Run generations
            job.status = "running"
            executor = SolveScenarioExecutor(self._settings, migrations_dir=self._migrations_dir)
            summary = executor.execute(
                scenario_name=created.scenario_name,
                family_name=created.family_name,
                generations=job.generations,
            )
            job.progress = summary.generations_executed

            # 3. Export skill package
            ctx = MtsToolContext(self._settings)
            job.result = export_skill_package(ctx, created.scenario_name)
            job.status = "completed"

        except Exception as exc:
            logger.exception("Solve job %s failed", job.job_id)
            job.status = "failed"
            job.error = str(exc)

    def _build_creator(self) -> SolveScenarioBuilder | None:
        """Build a family-aware solve scenario creator."""
        try:
            from autocontext.agents.llm_client import build_client_from_settings
            from autocontext.agents.subagent_runtime import SubagentRuntime

            client = build_client_from_settings(self._settings)
            runtime = SubagentRuntime(client)
            designer_model = self._settings.model_translator or self._settings.model_architect
            llm_fn = _llm_fn_from_client(client, designer_model)
            return SolveScenarioBuilder(
                runtime=runtime,
                llm_fn=llm_fn,
                model=designer_model,
                knowledge_root=self._settings.knowledge_root,
            )
        except Exception:
            logger.warning("failed to build solve scenario creator", exc_info=True)
            return None

    def get_status(self, job_id: str) -> dict[str, Any]:
        """Return current status of a solve job."""
        job = self._jobs.get(job_id)
        if job is None:
            return {"error": f"Job '{job_id}' not found"}
        return {
            "job_id": job.job_id,
            "status": job.status,
            "description": job.description,
            "scenario_name": job.scenario_name,
            "generations": job.generations,
            "progress": job.progress,
            "error": job.error,
            "created_at": job.created_at,
        }

    def get_result(self, job_id: str) -> SkillPackage | None:
        """Return the skill package if the job is completed, otherwise None."""
        job = self._jobs.get(job_id)
        if job is None or job.status != "completed":
            return None
        return job.result
