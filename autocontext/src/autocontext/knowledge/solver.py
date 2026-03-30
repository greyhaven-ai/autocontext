"""Solve-on-demand — background scenario creation and strategy evolution."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autocontext.config.settings import AppSettings
from autocontext.knowledge.export import SkillPackage, export_skill_package
from autocontext.mcp.tools import MtsToolContext

logger = logging.getLogger(__name__)


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
            creator = self._build_creator()
            if creator is None:
                job.status = "failed"
                job.error = "ScenarioCreator unavailable (no API key or unsupported provider)"
                return

            spec = creator.generate_spec(job.description)
            build = creator.build_and_validate(spec)

            from autocontext.scenarios import SCENARIO_REGISTRY

            SCENARIO_REGISTRY[spec.name] = build.scenario_class
            job.scenario_name = spec.name

            # 2. Run generations
            job.status = "running"
            from autocontext.loop.generation_runner import GenerationRunner

            runner = GenerationRunner(self._settings)
            runner.migrate(self._migrations_dir)
            run_id = f"solve_{spec.name}_{uuid.uuid4().hex[:8]}"
            summary = runner.run(spec.name, job.generations, run_id)
            job.progress = summary.generations_executed

            # 3. Export skill package
            ctx = MtsToolContext(self._settings)
            job.result = export_skill_package(ctx, spec.name)
            job.status = "completed"

        except Exception as exc:
            logger.exception("Solve job %s failed", job.job_id)
            job.status = "failed"
            job.error = str(exc)

    def _build_creator(self) -> Any:
        """Build a ScenarioCreator following the same pattern as server/app.py."""
        try:
            from autocontext.agents.llm_client import build_client_from_settings
            from autocontext.agents.subagent_runtime import SubagentRuntime
            from autocontext.scenarios.custom.creator import ScenarioCreator

            client = build_client_from_settings(self._settings)
            runtime = SubagentRuntime(client)
            return ScenarioCreator(
                runtime=runtime,
                model=self._settings.model_architect,
                knowledge_root=self._settings.knowledge_root,
            )
        except Exception:
            logger.warning("failed to build ScenarioCreator for solve job", exc_info=True)
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
