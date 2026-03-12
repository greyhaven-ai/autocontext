"""Sandbox manager for isolated external play."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from autocontext.config.settings import AppSettings
from autocontext.loop.generation_runner import GenerationRunner
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.storage.artifacts import ArtifactStore


@dataclass(slots=True)
class Sandbox:
    sandbox_id: str
    user_id: str
    scenario_name: str
    root: Path
    settings: AppSettings


class SandboxManager:
    """Manage isolated sandbox environments for external MCP users."""

    def __init__(self, base_settings: AppSettings) -> None:
        self._base = base_settings
        self._root = Path(base_settings.runs_root).parent / "sandboxes"
        self._active: dict[str, Sandbox] = {}

    def create(self, scenario_name: str, user_id: str = "anonymous") -> Sandbox:
        """Create isolated sandbox with knowledge seeded from main."""
        if scenario_name not in SCENARIO_REGISTRY:
            supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
            raise ValueError(f"Unknown scenario '{scenario_name}'. Supported: {supported}")

        sandbox_id = f"sbx_{user_id}_{uuid.uuid4().hex[:8]}"
        sandbox_root = self._root / sandbox_id

        # Create sandbox directory structure
        sb_runs = sandbox_root / "runs"
        sb_knowledge = sandbox_root / "knowledge"
        sb_skills = sandbox_root / "skills"
        sb_claude_skills = sandbox_root / ".claude" / "skills"
        sb_migrations = sandbox_root / "migrations"
        for d in [sb_runs, sb_knowledge, sb_skills, sb_claude_skills, sb_migrations]:
            d.mkdir(parents=True, exist_ok=True)

        # Copy migrations from the main package
        # migrations/ is at the package root (sibling of src/), i.e. parents[3] from this file
        main_migrations = Path(__file__).resolve().parents[3] / "migrations"
        if main_migrations.exists():
            for f in sorted(main_migrations.glob("*.sql")):
                shutil.copy2(f, sb_migrations / f.name)

        # Create sandbox-scoped settings
        sb_settings = AppSettings(
            db_path=sb_runs / "autocontext.sqlite3",
            runs_root=sb_runs,
            knowledge_root=sb_knowledge,
            skills_root=sb_skills,
            claude_skills_path=sb_claude_skills,
            executor_mode="local",
            agent_provider=self._base.agent_provider,
            anthropic_api_key=self._base.anthropic_api_key,
            model_competitor=self._base.model_competitor,
            model_analyst=self._base.model_analyst,
            model_coach=self._base.model_coach,
            model_architect=self._base.model_architect,
            model_translator=self._base.model_translator,
            model_curator=self._base.model_curator,
            matches_per_generation=self._base.matches_per_generation,
            backpressure_min_delta=self._base.backpressure_min_delta,
            max_retries=1,
            cross_run_inheritance=False,
            curator_enabled=self._base.curator_enabled,
            playbook_max_versions=self._base.playbook_max_versions,
            event_stream_path=sb_runs / "events.ndjson",
            sandbox_max_generations=self._base.sandbox_max_generations,
        )

        # Seed knowledge from main
        self._seed_knowledge(scenario_name, sb_knowledge)

        sandbox = Sandbox(
            sandbox_id=sandbox_id,
            user_id=user_id,
            scenario_name=scenario_name,
            root=sandbox_root,
            settings=sb_settings,
        )
        self._active[sandbox_id] = sandbox
        return sandbox

    def _seed_knowledge(self, scenario_name: str, sb_knowledge: Path) -> None:
        """Copy playbook, hints, and tools from main knowledge to sandbox."""
        main_knowledge = self._base.knowledge_root / scenario_name
        sb_scenario = sb_knowledge / scenario_name
        sb_scenario.mkdir(parents=True, exist_ok=True)

        # Copy playbook
        playbook = main_knowledge / "playbook.md"
        if playbook.exists():
            (sb_scenario / "playbook.md").write_text(
                playbook.read_text(encoding="utf-8"), encoding="utf-8"
            )

        # Copy hints
        hints = main_knowledge / "hints.md"
        if hints.exists():
            (sb_scenario / "hints.md").write_text(
                hints.read_text(encoding="utf-8"), encoding="utf-8"
            )

        # Copy tools
        tools_dir = main_knowledge / "tools"
        if tools_dir.exists():
            sb_tools = sb_scenario / "tools"
            sb_tools.mkdir(parents=True, exist_ok=True)
            for f in tools_dir.glob("*.py"):
                shutil.copy2(f, sb_tools / f.name)

    def run_generation(self, sandbox_id: str, generations: int = 1) -> dict[str, object]:
        """Run generation(s) in sandbox isolation."""
        sandbox = self._active.get(sandbox_id)
        if sandbox is None:
            raise ValueError(f"Sandbox '{sandbox_id}' not found")

        if generations > sandbox.settings.sandbox_max_generations:
            raise ValueError(
                f"Requested {generations} generations exceeds sandbox limit of "
                f"{sandbox.settings.sandbox_max_generations}"
            )

        runner = GenerationRunner(sandbox.settings)
        sb_migrations = sandbox.root / "migrations"
        if sb_migrations.exists():
            runner.migrate(sb_migrations)

        summary = runner.run(
            scenario_name=sandbox.scenario_name,
            generations=generations,
        )
        return {
            "sandbox_id": sandbox_id,
            "run_id": summary.run_id,
            "scenario": summary.scenario,
            "generations_executed": summary.generations_executed,
            "best_score": summary.best_score,
            "current_elo": summary.current_elo,
        }

    def get_status(self, sandbox_id: str) -> dict[str, object]:
        """Get sandbox status."""
        sandbox = self._active.get(sandbox_id)
        if sandbox is None:
            raise ValueError(f"Sandbox '{sandbox_id}' not found")
        return {
            "sandbox_id": sandbox.sandbox_id,
            "user_id": sandbox.user_id,
            "scenario_name": sandbox.scenario_name,
            "root": str(sandbox.root),
        }

    def read_playbook(self, sandbox_id: str) -> str:
        """Read sandbox playbook."""
        sandbox = self._active.get(sandbox_id)
        if sandbox is None:
            raise ValueError(f"Sandbox '{sandbox_id}' not found")
        artifacts = ArtifactStore(
            sandbox.settings.runs_root,
            sandbox.settings.knowledge_root,
            sandbox.settings.skills_root,
            sandbox.settings.claude_skills_path,
            max_playbook_versions=sandbox.settings.playbook_max_versions,
        )
        return artifacts.read_playbook(sandbox.scenario_name)

    def list_sandboxes(self) -> list[dict[str, str]]:
        """List active sandboxes."""
        return [
            {
                "sandbox_id": sb.sandbox_id,
                "user_id": sb.user_id,
                "scenario_name": sb.scenario_name,
            }
            for sb in self._active.values()
        ]

    def destroy(self, sandbox_id: str) -> bool:
        """Remove sandbox and all its data."""
        sandbox = self._active.pop(sandbox_id, None)
        if sandbox is None:
            return False
        if sandbox.root.exists():
            shutil.rmtree(sandbox.root)
        return True
