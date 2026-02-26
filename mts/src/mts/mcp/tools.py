"""MCP tool implementations — thin wrappers around existing MTS infrastructure."""

from __future__ import annotations

from mts.config import AppSettings
from mts.knowledge.trajectory import ScoreTrajectoryBuilder
from mts.scenarios import SCENARIO_REGISTRY
from mts.storage import ArtifactStore, SQLiteStore


class MtsToolContext:
    """Lazy-initialized shared state for MCP tool implementations."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.sqlite = SQLiteStore(settings.db_path)
        self.artifacts = ArtifactStore(
            settings.runs_root,
            settings.knowledge_root,
            settings.skills_root,
            settings.claude_skills_path,
            max_playbook_versions=settings.playbook_max_versions,
        )
        self.trajectory = ScoreTrajectoryBuilder(self.sqlite)


# -- Scenario exploration --


def list_scenarios() -> list[dict[str, str]]:
    """Return scenario names with descriptions."""
    results: list[dict[str, str]] = []
    for name, cls in SCENARIO_REGISTRY.items():
        instance = cls()
        if hasattr(instance, "describe_rules"):
            preview = instance.describe_rules()[:200]
        elif hasattr(instance, "describe_task"):
            preview = instance.describe_task()[:200]
        else:
            preview = ""
        results.append({
            "name": name,
            "rules_preview": preview,
        })
    return results


def describe_scenario(name: str) -> dict[str, str]:
    """Full scenario description: rules, strategy interface, evaluation criteria."""
    scenario = SCENARIO_REGISTRY[name]()
    if hasattr(scenario, "describe_rules"):
        return {
            "rules": scenario.describe_rules(),
            "strategy_interface": scenario.describe_strategy_interface(),
            "evaluation_criteria": scenario.describe_evaluation_criteria(),
        }
    return {
        "rules": scenario.describe_task() if hasattr(scenario, "describe_task") else "",
        "strategy_interface": "",
        "evaluation_criteria": scenario.get_rubric() if hasattr(scenario, "get_rubric") else "",
    }


def validate_strategy(name: str, strategy: dict[str, object]) -> dict[str, object]:
    """Validate a strategy dict against scenario constraints."""
    scenario = SCENARIO_REGISTRY[name]()
    if not hasattr(scenario, "validate_actions"):
        return {"valid": True, "reason": "Agent task scenarios use judge evaluation, not action validation"}
    state = scenario.initial_state(seed=42)
    valid, reason = scenario.validate_actions(state, "challenger", strategy)
    return {"valid": valid, "reason": reason}


def run_match(name: str, strategy: dict[str, object], seed: int) -> dict[str, object]:
    """Execute a single match, return Result as dict."""
    scenario = SCENARIO_REGISTRY[name]()
    if not hasattr(scenario, "execute_match"):
        return {"error": "Agent task scenarios use judge evaluation; use evaluate_output() instead"}
    result = scenario.execute_match(strategy, seed)
    return result.model_dump()


def run_tournament(name: str, strategy: dict[str, object], matches: int, seed_base: int) -> dict[str, object]:
    """Run N matches, return aggregate stats."""
    scenario = SCENARIO_REGISTRY[name]()
    if not hasattr(scenario, "execute_match"):
        return {"error": "Agent task scenarios use judge evaluation; use evaluate_output() instead"}
    scores: list[float] = []
    for i in range(matches):
        result = scenario.execute_match(strategy, seed_base + i)
        scores.append(result.score)
    return {
        "matches": matches,
        "scores": scores,
        "mean_score": sum(scores) / len(scores) if scores else 0.0,
        "best_score": max(scores) if scores else 0.0,
    }


# -- Knowledge reading --


def read_playbook(ctx: MtsToolContext, scenario_name: str) -> str:
    """Read current strategy playbook for a scenario."""
    return ctx.artifacts.read_playbook(scenario_name)


def read_trajectory(ctx: MtsToolContext, run_id: str) -> str:
    """Read score trajectory table for a run."""
    return ctx.trajectory.build_trajectory(run_id) or "No trajectory data yet."


def read_analysis(ctx: MtsToolContext, scenario_name: str, generation: int) -> str:
    """Read analysis for a specific generation."""
    analysis_path = ctx.artifacts.knowledge_root / scenario_name / "analysis" / f"gen_{generation}.md"
    if not analysis_path.exists():
        return ""
    return analysis_path.read_text(encoding="utf-8")


def read_hints(ctx: MtsToolContext, scenario_name: str) -> str:
    """Read persisted coach hints."""
    return ctx.artifacts.read_hints(scenario_name)


def read_tool_context(ctx: MtsToolContext, scenario_name: str) -> str:
    """Read architect-generated tools."""
    return ctx.artifacts.read_tool_context(scenario_name)


def read_skills(ctx: MtsToolContext, scenario_name: str) -> str:
    """Read operational lessons from SKILL.md."""
    return ctx.artifacts.read_skills(scenario_name)


# -- Run management --


def list_runs(ctx: MtsToolContext) -> list[dict[str, object]]:
    """List recent runs from SQLite."""
    with ctx.sqlite.connect() as conn:
        rows = conn.execute(
            "SELECT run_id, scenario, target_generations, executor_mode, status, created_at "
            "FROM runs ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    return [dict(row) for row in rows]


def run_status(ctx: MtsToolContext, run_id: str) -> list[dict[str, object]]:
    """Get generation-level metrics for a run."""
    return ctx.sqlite.get_generation_metrics(run_id)


def run_replay(ctx: MtsToolContext, run_id: str, generation: int) -> dict[str, object]:
    """Read replay JSON for a specific generation."""
    import json

    replay_dir = ctx.settings.runs_root / run_id / "generations" / f"gen_{generation}" / "replays"
    if not replay_dir.exists():
        return {"error": f"no replay directory for run={run_id} gen={generation}"}
    replay_files = sorted(replay_dir.glob("*.json"))
    if not replay_files:
        return {"error": f"no replay files under {replay_dir}"}
    return json.loads(replay_files[0].read_text(encoding="utf-8"))  # type: ignore[no-any-return]


# -- Knowledge API --


def export_skill(ctx: MtsToolContext, scenario_name: str) -> dict[str, object]:
    """Export a portable skill package for a solved scenario."""
    from mts.knowledge.export import export_skill_package

    pkg = export_skill_package(ctx, scenario_name)
    return pkg.to_dict()


def list_solved(ctx: MtsToolContext) -> list[dict[str, object]]:
    """List scenarios with solved strategies."""
    from mts.knowledge.export import list_solved_scenarios

    return list_solved_scenarios(ctx)


def search_strategies(ctx: MtsToolContext, query: str, top_k: int = 5) -> list[dict[str, object]]:
    """Search solved scenarios by query."""
    from mts.knowledge.search import search_strategies as _search

    results = _search(ctx, query, top_k)
    return [
        {
            "scenario": r.scenario_name,
            "display_name": r.display_name,
            "description": r.description,
            "relevance": r.relevance_score,
            "best_score": r.best_score,
            "best_elo": r.best_elo,
            "match_reason": r.match_reason,
        }
        for r in results
    ]


# -- Human feedback --


def record_feedback(
    ctx: MtsToolContext,
    scenario_name: str,
    agent_output: str,
    human_score: float | None = None,
    human_notes: str = "",
    generation_id: str | None = None,
) -> dict[str, object]:
    """Record human feedback on an agent task output."""
    if not agent_output.strip():
        return {"error": "agent_output cannot be empty"}
    if human_score is not None and not (0.0 <= human_score <= 1.0):
        return {"error": f"human_score must be in [0.0, 1.0], got {human_score}"}
    row_id = ctx.sqlite.insert_human_feedback(
        scenario_name=scenario_name,
        agent_output=agent_output,
        human_score=human_score,
        human_notes=human_notes,
        generation_id=generation_id,
    )
    return {"id": row_id, "scenario_name": scenario_name, "status": "recorded"}


def get_feedback(
    ctx: MtsToolContext,
    scenario_name: str,
    limit: int = 10,
) -> list[dict[str, object]]:
    """Get recent human feedback for a scenario."""
    return ctx.sqlite.get_human_feedback(scenario_name, limit=limit)  # type: ignore[return-value]
