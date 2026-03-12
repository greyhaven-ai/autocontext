from __future__ import annotations

import json
from typing import Any

from autocontext.rlm.types import RlmContext
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore


class ContextLoader:
    """Loads run data into REPL namespace variables for RLM-enabled agents."""

    def __init__(self, artifacts: ArtifactStore, sqlite: SQLiteStore) -> None:
        self._artifacts = artifacts
        self._sqlite = sqlite

    @property
    def sqlite(self) -> SQLiteStore:
        """Expose the SQLite store for trial summary persistence."""
        return self._sqlite

    def load_for_analyst(
        self,
        run_id: str,
        scenario_name: str,
        generation: int,
        *,
        scenario_rules: str = "",
        strategy_interface: str = "",
        current_strategy: dict[str, Any] | None = None,
    ) -> RlmContext:
        """Build the REPL namespace for the Analyst role."""
        variables: dict[str, Any] = {}

        variables["replays"] = self._load_replays(run_id, generation)
        variables["metrics_history"] = self._load_metrics_files(run_id, generation)
        variables["match_scores"] = self._sqlite.get_matches_for_run(run_id)
        variables["playbook"] = self._artifacts.read_playbook(scenario_name)
        variables["scenario_rules"] = scenario_rules
        variables["strategy_interface"] = strategy_interface
        variables["current_strategy"] = current_strategy or {}
        variables["prior_analyses"] = self._load_prior_analyses(scenario_name, generation)
        variables["operational_lessons"] = self._artifacts.read_skills(scenario_name)

        summary = self._build_analyst_summary(variables)
        return RlmContext(variables=variables, summary=summary)

    def load_for_architect(
        self,
        run_id: str,
        scenario_name: str,
        generation: int,
        *,
        scenario_rules: str = "",
    ) -> RlmContext:
        """Build the REPL namespace for the Architect role."""
        variables: dict[str, Any] = {}

        variables["existing_tools"] = self._load_tool_sources(scenario_name)
        variables["metrics_history"] = self._load_metrics_files(run_id, generation)
        variables["replays"] = self._load_replays(run_id, generation, latest_only=True)
        variables["playbook"] = self._artifacts.read_playbook(scenario_name)
        variables["architect_changelog"] = self._load_architect_changelog(scenario_name)
        variables["scenario_rules"] = scenario_rules
        variables["match_scores"] = self._sqlite.get_matches_for_run(run_id)
        variables["operational_lessons"] = self._artifacts.read_skills(scenario_name)

        summary = self._build_architect_summary(variables)
        return RlmContext(variables=variables, summary=summary)

    def load_for_competitor(
        self,
        run_id: str,
        scenario_name: str,
        generation: int,
        *,
        scenario_rules: str = "",
        strategy_interface: str = "",
        current_strategy: dict[str, Any] | None = None,
    ) -> RlmContext:
        """Build the REPL namespace for the Competitor role."""
        variables: dict[str, Any] = {}

        # Match replays (all generations up to current)
        variables["replays"] = self._load_replays(run_id, generation)

        # Metrics history
        variables["metrics_history"] = self._load_metrics_files(run_id, generation)

        # Match scores from DB
        variables["match_scores"] = self._sqlite.get_matches_for_run(run_id)

        # Strategy guidance
        variables["playbook"] = self._artifacts.read_playbook(scenario_name)
        variables["coach_hints"] = self._artifacts.read_hints(scenario_name)

        # Scenario context
        variables["scenario_rules"] = scenario_rules
        variables["strategy_interface"] = strategy_interface
        variables["current_strategy"] = current_strategy or {}

        # Prior analyses
        variables["prior_analyses"] = self._load_prior_analyses(scenario_name, generation)
        variables["operational_lessons"] = self._artifacts.read_skills(scenario_name)

        summary = self._build_competitor_summary(variables)
        return RlmContext(variables=variables, summary=summary)

    # ------------------------------------------------------------------
    # Data loading helpers
    # ------------------------------------------------------------------

    def _load_replays(self, run_id: str, generation: int, *, latest_only: bool = False) -> list[dict[str, Any]]:
        replays: list[dict[str, Any]] = []
        start = generation if latest_only else 1
        for gen_idx in range(start, generation + 1):
            gen_dir = self._artifacts.generation_dir(run_id, gen_idx)
            replay_dir = gen_dir / "replays"
            if not replay_dir.exists():
                continue
            for rfile in sorted(replay_dir.glob("*.json")):
                try:
                    replays.append(json.loads(rfile.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    continue
        return replays

    def _load_metrics_files(self, run_id: str, generation: int) -> list[dict[str, Any]]:
        metrics: list[dict[str, Any]] = []
        for gen_idx in range(1, generation + 1):
            mpath = self._artifacts.generation_dir(run_id, gen_idx) / "metrics.json"
            if not mpath.exists():
                continue
            try:
                metrics.append(json.loads(mpath.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return metrics

    def _load_prior_analyses(self, scenario_name: str, generation: int) -> list[str]:
        analyses: list[str] = []
        analysis_dir = self._artifacts.knowledge_root / scenario_name / "analysis"
        if not analysis_dir.exists():
            return analyses
        for gen_idx in range(1, generation + 1):
            apath = analysis_dir / f"gen_{gen_idx}.md"
            if apath.exists():
                try:
                    analyses.append(apath.read_text(encoding="utf-8"))
                except OSError:
                    continue
        return analyses

    def _load_tool_sources(self, scenario_name: str) -> dict[str, str]:
        tools: dict[str, str] = {}
        tool_dir = self._artifacts.tools_dir(scenario_name)
        if not tool_dir.exists():
            return tools
        for tfile in sorted(tool_dir.glob("*.py")):
            try:
                tools[tfile.stem] = tfile.read_text(encoding="utf-8")
            except OSError:
                continue
        return tools

    def _load_architect_changelog(self, scenario_name: str) -> str:
        changelog_path = self._artifacts.knowledge_root / scenario_name / "architect" / "changelog.md"
        if not changelog_path.exists():
            return ""
        try:
            return changelog_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    # ------------------------------------------------------------------
    # Summary builders
    # ------------------------------------------------------------------

    def _build_analyst_summary(self, variables: dict[str, Any]) -> str:
        lines = [
            f"- `replays`: list of {len(variables['replays'])} replay dicts",
            f"- `metrics_history`: list of {len(variables['metrics_history'])} generation metrics dicts",
            f"- `match_scores`: list of {len(variables['match_scores'])} match score records from DB",
            f"- `playbook`: string ({len(variables['playbook'])} chars) — accumulated strategy guidance",
            f"- `scenario_rules`: string ({len(variables['scenario_rules'])} chars)",
            f"- `strategy_interface`: string ({len(variables['strategy_interface'])} chars)",
            f"- `current_strategy`: dict with {len(variables['current_strategy'])} keys",
            f"- `prior_analyses`: list of {len(variables['prior_analyses'])} previous analysis markdown strings",
            f"- `operational_lessons`: string ({len(variables['operational_lessons'])} chars) — lessons from prior gens",
        ]
        return "\n".join(lines)

    def _build_architect_summary(self, variables: dict[str, Any]) -> str:
        tool_names = list(variables["existing_tools"].keys())
        lines = [
            f"- `existing_tools`: dict of {len(tool_names)} tools — {', '.join(tool_names) if tool_names else 'none'}",
            f"- `metrics_history`: list of {len(variables['metrics_history'])} generation metrics dicts",
            f"- `replays`: list of {len(variables['replays'])} replay dicts (latest generation)",
            f"- `playbook`: string ({len(variables['playbook'])} chars)",
            f"- `architect_changelog`: string ({len(variables['architect_changelog'])} chars)",
            f"- `scenario_rules`: string ({len(variables['scenario_rules'])} chars)",
            f"- `match_scores`: list of {len(variables['match_scores'])} match score records",
            f"- `operational_lessons`: string ({len(variables['operational_lessons'])} chars) — lessons from prior gens",
        ]
        return "\n".join(lines)

    def _build_competitor_summary(self, variables: dict[str, Any]) -> str:
        lines = [
            f"- `replays`: list of {len(variables['replays'])} replay dicts",
            f"- `metrics_history`: list of {len(variables['metrics_history'])} generation metrics",
            f"- `match_scores`: list of {len(variables['match_scores'])} match score records",
            f"- `playbook`: string ({len(variables['playbook'])} chars) — strategy guidance",
            f"- `coach_hints`: string ({len(variables['coach_hints'])} chars) — competitor hints",
            f"- `scenario_rules`: string ({len(variables['scenario_rules'])} chars)",
            f"- `strategy_interface`: string ({len(variables['strategy_interface'])} chars)",
            "- `current_strategy`: dict — current generation's strategy",
            f"- `prior_analyses`: list of {len(variables['prior_analyses'])} analysis strings",
            f"- `operational_lessons`: string ({len(variables['operational_lessons'])} chars)",
        ]
        return "\n".join(lines)
