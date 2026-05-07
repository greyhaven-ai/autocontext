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
from autocontext.extensions import HookBus, active_hook_bus, wrap_language_model_client
from autocontext.knowledge.export import SkillPackage, export_skill_package
from autocontext.knowledge.solve_agent_task_design import (
    _SOLVE_AGENT_TASK_DESIGN_MAX_CHARS,  # noqa: F401 - re-exported for existing tests/imports
    RETRY_SOLVE_AGENT_TASK_DESIGNER_SYSTEM,
    SOLVE_AGENT_TASK_DESIGNER_SYSTEM,
    _build_solve_agent_task_design_brief,
    _build_solve_description_brief,
    _solve_task_spec_needs_compact_retry,
)
from autocontext.knowledge.solve_task_execution import SolveExecutionSummary, run_task_like_scenario
from autocontext.loop.runner_hooks import initialize_hook_bus
from autocontext.mcp.tools import MtsToolContext
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.scenarios.artifact_editing import Artifact, ArtifactEditingInterface
from autocontext.scenarios.custom.classifier_cache import (
    ClassifierCache,
    default_classifier_cache_path,
)

if TYPE_CHECKING:
    from autocontext.scenarios.families import ScenarioFamily

logger = logging.getLogger(__name__)


class _NamedScenario(Protocol):
    name: str


_FAMILY_HEADER_RE = re.compile(r"^\s*\*{0,2}family\*{0,2}:\s*(?P<body>.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_SOLVE_FAMILY_ALIASES = {
    "alignment_stress_test": "agent_task",
    "meta_learning": "agent_task",
    "capability_bootstrapping": "agent_task",
    "compositional_generalization": "agent_task",
}
_SIMULATION_INTERFACE_HINT_RE = re.compile(
    r"\bsimulationinterface\b.*\bworldstate\b|\bworldstate\b.*\bsimulationinterface\b",
    re.IGNORECASE | re.DOTALL,
)
_AGENT_TASK_INTERFACE_HINT_RE = re.compile(r"\bagent[- ]task evaluation\b", re.IGNORECASE)
_SOLVE_CREATOR_PI_TIMEOUT_FLOOR_SECONDS = 600.0


@dataclass
class SolveJob:
    job_id: str
    description: str
    scenario_name: str | None = None
    family_name: str | None = None
    status: str = "pending"
    generations: int = 5
    progress: int = 0
    error: str | None = None
    result: SkillPackage | None = None
    created_at: float = field(default_factory=time.time)
    family_override: str | None = None
    llm_classifier_fallback_used: bool = False
    # AC-734: when set, bypass the LLM scenario designer and use this
    # text verbatim as the agent task_prompt. Preserves long, detail-laden
    # descriptions (e.g. Lean lemma signatures) that the designer would
    # otherwise truncate or generalize away.
    verbatim_task_prompt: str | None = None


@dataclass(slots=True)
class SolveScenarioBuildResult:
    scenario_name: str
    family_name: str
    llm_classifier_fallback_used: bool = False


@dataclass(slots=True)
class _ResolvedSolveFamily:
    family: ScenarioFamily
    llm_classifier_fallback_used: bool = False


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
    from autocontext.scenarios.custom.family_classifier import resolve_direct_family_hint
    from autocontext.scenarios.families import get_family

    match = _FAMILY_HEADER_RE.search(description)
    if match is not None:
        for token in re.split(r"[/,|]", match.group("body")):
            candidate = _normalize_family_hint_token(token)
            aliased = _SOLVE_FAMILY_ALIASES.get(candidate)
            if aliased is not None:
                return get_family(aliased)

    direct_family = resolve_direct_family_hint(description)
    if direct_family is not None:
        return get_family(direct_family)
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
    return _resolve_requested_scenario_family_with_metadata(description, llm_fn=llm_fn).family


def _resolve_requested_scenario_family_with_metadata(
    description: str,
    *,
    llm_fn: LlmFn | None = None,
    cache: ClassifierCache | None = None,
) -> _ResolvedSolveFamily:
    from autocontext.scenarios.custom.family_classifier import classify_scenario_family, route_to_family

    brief = _build_solve_description_brief(description)
    hinted_family = _resolve_family_hint(brief)
    if hinted_family is not None:
        return _ResolvedSolveFamily(family=hinted_family)

    aliased_family = _resolve_solve_family_alias(brief)
    if aliased_family is not None:
        return _ResolvedSolveFamily(family=aliased_family)

    classification = classify_scenario_family(brief, llm_fn=llm_fn, cache=cache)
    return _ResolvedSolveFamily(
        family=route_to_family(classification),
        llm_classifier_fallback_used=classification.llm_classifier_used,
    )


class SolveScenarioExecutor:
    """Execute created solve scenarios through the correct family-aware runtime surface."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        migrations_dir: Path | None = None,
        hook_bus: HookBus | None = None,
        loaded_extensions: list[str] | None = None,
    ) -> None:
        self._settings = settings
        self._migrations_dir = migrations_dir or Path(__file__).resolve().parents[2] / "migrations"
        if hook_bus is None:
            self._hook_bus, self._loaded_extensions = initialize_hook_bus(settings)
        else:
            self._hook_bus = hook_bus
            self._loaded_extensions = list(loaded_extensions or [])

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

        runner = GenerationRunner(
            _settings_for_solve_runtime(self._settings, respect_generation_budget=True),
            hook_bus=self._hook_bus,
            loaded_extensions=self._loaded_extensions,
        )
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
        return run_task_like_scenario(
            settings=self._settings,
            runtime_settings=_settings_for_solve_runtime(self._settings, respect_generation_budget=True),
            migrations_dir=self._migrations_dir,
            scenario_name=scenario_name,
            scenario_type=scenario_type,
            task=task,
            max_rounds=max_rounds,
            hook_bus=self._hook_bus,
            loaded_extensions=self._loaded_extensions,
            role_runtime_resolver=resolve_role_runtime,
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
            llm_classifier_fallback_used = False
        else:
            cache = ClassifierCache(default_classifier_cache_path(self._knowledge_root))
            resolved_family = _resolve_requested_scenario_family_with_metadata(
                brief,
                llm_fn=self._llm_fn,
                cache=cache,
            )
            family = resolved_family.family
            llm_classifier_fallback_used = resolved_family.llm_classifier_fallback_used

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
                llm_classifier_fallback_used=llm_classifier_fallback_used,
            )

        family_creator = AgentTaskCreator(
            llm_fn=self._llm_fn,
            knowledge_root=self._knowledge_root,
            designer_system_prompt=SOLVE_AGENT_TASK_DESIGNER_SYSTEM,
            retry_designer_system_prompt=RETRY_SOLVE_AGENT_TASK_DESIGNER_SYSTEM,
            description_transform=_build_solve_agent_task_design_brief,
            retry_spec_predicate=_solve_task_spec_needs_compact_retry,
        )
        scenario = family_creator.create(brief, family_name=family.name)
        scenario_name = str(cast(_NamedScenario, scenario).name)
        SCENARIO_REGISTRY[scenario_name] = scenario.__class__
        return SolveScenarioBuildResult(
            scenario_name=scenario_name,
            family_name=family.name,
            llm_classifier_fallback_used=llm_classifier_fallback_used,
        )


def _llm_fn_from_client(client: Any, model: str) -> LlmFn:
    def llm_fn(system: str, user: str) -> str:
        response = client.generate(
            model=model,
            prompt=f"{system}\n\n{user}",
            max_tokens=1200,
            temperature=0.2,
            role="scenario_designer",
        )
        response_text: object = getattr(response, "text", "")
        if not isinstance(response_text, str):
            response_text = str(response_text)
        return response_text.strip()

    return llm_fn


class SolveManager:
    """Manage solve-on-demand jobs: create scenario -> run generations -> export skill."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        hook_bus: HookBus | None = None,
        loaded_extensions: list[str] | None = None,
    ) -> None:
        self._jobs: dict[str, SolveJob] = {}
        self._settings = settings
        self._migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
        if hook_bus is None:
            self.hook_bus, self.loaded_extensions = initialize_hook_bus(settings)
        else:
            self.hook_bus = hook_bus
            self.loaded_extensions = list(loaded_extensions or [])

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
        verbatim_task_prompt: str | None = None,
    ) -> SolveJob:
        """Run solve-on-demand synchronously in the current process.

        If ``family_override`` is provided, the scenario family classifier is
        bypassed and the solver routes directly to the named family's pipeline.

        If ``verbatim_task_prompt`` is provided (AC-734), the LLM scenario
        designer is bypassed entirely; the supplied text becomes the
        compiled scenario's ``task_prompt`` verbatim. ``description`` is
        still used for the derived scenario name and logging.
        """
        job_id = f"solve_{uuid.uuid4().hex[:8]}"
        job = SolveJob(
            job_id=job_id,
            description=description,
            generations=generations,
            family_override=family_override,
            verbatim_task_prompt=verbatim_task_prompt,
        )
        self._jobs[job_id] = job
        self._run_job(job)
        return job

    def _run_job(self, job: SolveJob) -> None:
        """Background: create scenario -> run generations -> export skill package."""
        try:
            with active_hook_bus(self.hook_bus):
                # 1. Create scenario
                job.status = "creating_scenario"

                if job.verbatim_task_prompt is not None:
                    # AC-734: bypass the LLM designer entirely; the operator's
                    # text is the task_prompt. No provider/network required for
                    # the build step.
                    from autocontext.knowledge.verbatim_solve import (
                        VerbatimSolveRequest,
                        build_verbatim_solve_scenario,
                    )

                    created = build_verbatim_solve_scenario(
                        VerbatimSolveRequest(
                            description=job.description,
                            task_prompt=job.verbatim_task_prompt,
                        ),
                        knowledge_root=self._settings.knowledge_root,
                    )
                else:
                    builder = self._build_creator()
                    if builder is None:
                        job.status = "failed"
                        job.error = "Scenario creation pipeline unavailable (no API key or unsupported provider)"
                        return
                    created = builder.build(job.description, family_override=job.family_override)

                job.scenario_name = created.scenario_name
                job.family_name = created.family_name
                job.llm_classifier_fallback_used = created.llm_classifier_fallback_used

                # 2. Run generations
                job.status = "running"
                executor = SolveScenarioExecutor(
                    self._settings,
                    migrations_dir=self._migrations_dir,
                    hook_bus=self.hook_bus,
                    loaded_extensions=self.loaded_extensions,
                )
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

            creator_settings = _settings_for_solve_runtime(self._settings)
            client = wrap_language_model_client(
                build_client_from_settings(creator_settings),
                self.hook_bus,
                provider_name="solve:scenario_designer",
            )
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
            "family_name": job.family_name,
            "generations": job.generations,
            "progress": job.progress,
            "error": job.error,
            "created_at": job.created_at,
            "llm_classifier_fallback_used": job.llm_classifier_fallback_used,
        }

    def get_result(self, job_id: str) -> SkillPackage | None:
        """Return the skill package if the job is completed, otherwise None."""
        job = self._jobs.get(job_id)
        if job is None or job.status != "completed":
            return None
        return job.result


def _settings_for_solve_runtime(
    settings: AppSettings,
    *,
    respect_generation_budget: bool = False,
) -> AppSettings:
    if settings.agent_provider not in {"pi", "pi-rpc"}:
        return settings
    if respect_generation_budget and settings.generation_time_budget_seconds > 0:
        bounded_timeout = min(float(settings.pi_timeout), float(settings.generation_time_budget_seconds))
        if bounded_timeout == float(settings.pi_timeout):
            return settings
        return settings.model_copy(update={"pi_timeout": bounded_timeout})
    if float(settings.pi_timeout) >= _SOLVE_CREATOR_PI_TIMEOUT_FLOOR_SECONDS:
        return settings
    return settings.model_copy(update={"pi_timeout": _SOLVE_CREATOR_PI_TIMEOUT_FLOOR_SECONDS})
