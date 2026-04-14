"""Solve-on-demand — background scenario creation and strategy evolution."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from autocontext.agents.types import LlmFn
from autocontext.config.settings import AppSettings
from autocontext.knowledge.export import SkillPackage, export_skill_package
from autocontext.mcp.tools import MtsToolContext

logger = logging.getLogger(__name__)


class _NamedScenario(Protocol):
    name: str


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


@dataclass(slots=True)
class SolveScenarioBuildResult:
    scenario_name: str
    family_name: str


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

    def build(self, description: str) -> SolveScenarioBuildResult:
        from autocontext.scenarios import SCENARIO_REGISTRY
        from autocontext.scenarios.custom.agent_task_creator import AgentTaskCreator
        from autocontext.scenarios.custom.creator import ScenarioCreator
        from autocontext.scenarios.custom.family_classifier import classify_scenario_family, route_to_family

        classification = classify_scenario_family(description)
        family = route_to_family(classification)

        if family.name == "game":
            game_creator = ScenarioCreator(
                runtime=self._runtime,
                model=self._model,
                knowledge_root=self._knowledge_root,
            )
            spec = game_creator.generate_spec(description)
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
        scenario = family_creator.create(description)
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
            max_tokens=3000,
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

    def solve_sync(self, description: str, generations: int = 5) -> SolveJob:
        """Run solve-on-demand synchronously in the current process."""
        job_id = f"solve_{uuid.uuid4().hex[:8]}"
        job = SolveJob(
            job_id=job_id,
            description=description,
            generations=generations,
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

            created = builder.build(job.description)
            job.scenario_name = created.scenario_name

            # 2. Run generations
            job.status = "running"
            from autocontext.loop.generation_runner import GenerationRunner

            runner = GenerationRunner(self._settings)
            runner.migrate(self._migrations_dir)
            run_id = f"solve_{created.scenario_name}_{uuid.uuid4().hex[:8]}"
            summary = runner.run(created.scenario_name, job.generations, run_id)
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
            llm_fn = _llm_fn_from_client(client, self._settings.model_architect)
            return SolveScenarioBuilder(
                runtime=runtime,
                llm_fn=llm_fn,
                model=self._settings.model_architect,
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
