"""REST API router for the Strategy Knowledge API."""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from autocontext.config import load_settings
from autocontext.knowledge.export import export_skill_package, list_solved_scenarios
from autocontext.knowledge.search import search_strategies
from autocontext.knowledge.solver import SolveManager
from autocontext.mcp.tools import MtsToolContext

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_ctx: MtsToolContext | None = None
_solve_mgr: SolveManager | None = None


def _get_ctx() -> MtsToolContext:
    global _ctx
    if _ctx is None:
        _ctx = MtsToolContext(load_settings())
    return _ctx


def _get_solve_mgr() -> SolveManager:
    global _solve_mgr
    if _solve_mgr is None:
        _solve_mgr = SolveManager(_get_ctx().settings)
    return _solve_mgr


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class SolveRequest(BaseModel):
    description: str
    generations: int = Field(default=5, ge=1, le=50)


@router.get("/scenarios")
def list_solved() -> list[dict[str, Any]]:
    """List scenarios with solved strategies."""
    return list_solved_scenarios(_get_ctx())


@router.get("/export/{scenario_name}")
def export_skill(scenario_name: str, format: str = "skill") -> dict[str, Any]:
    """Export skill package for a scenario.

    Args:
        format: "skill" (legacy SkillPackage) or "package" (versioned StrategyPackage).
    """
    try:
        if format == "package":
            from autocontext.knowledge.export import export_strategy_package

            strategy_pkg = export_strategy_package(_get_ctx(), scenario_name)
            return cast(dict[str, Any], json.loads(strategy_pkg.to_json()))
        skill_pkg = export_skill_package(_get_ctx(), scenario_name)
        result = skill_pkg.to_dict()
        result["skill_markdown"] = skill_pkg.to_skill_markdown()
        result["suggested_filename"] = f"{scenario_name.replace('_', '-')}-knowledge.md"
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ImportRequest(BaseModel):
    package: dict[str, Any]
    conflict_policy: str = Field(default="merge", pattern="^(overwrite|merge|skip)$")


@router.post("/import")
def import_package(body: ImportRequest) -> dict[str, Any]:
    """Import a strategy package into scenario knowledge."""
    from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

    try:
        pkg = StrategyPackage.from_dict(body.package)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid package: {exc}") from exc
    policy = ConflictPolicy(body.conflict_policy)
    ctx = _get_ctx()
    result = import_strategy_package(ctx.artifacts, pkg, sqlite=ctx.sqlite, conflict_policy=policy)
    return result.model_dump()


@router.post("/search")
def search(body: SearchRequest) -> list[dict[str, Any]]:
    """Search strategies by natural language query."""
    results = search_strategies(_get_ctx(), body.query, body.top_k)
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


@router.post("/solve")
def submit_solve(body: SolveRequest) -> dict[str, Any]:
    """Submit a problem for on-demand solving."""
    mgr = _get_solve_mgr()
    job_id = mgr.submit(body.description, body.generations)
    return {"job_id": job_id, "status": "pending"}


@router.get("/solve/{job_id}")
def solve_status(job_id: str) -> dict[str, Any]:
    """Check solve job status and get result when completed."""
    mgr = _get_solve_mgr()
    status = mgr.get_status(job_id)
    if "error" in status and status.get("status") is None:
        raise HTTPException(status_code=404, detail=status["error"])
    result = mgr.get_result(job_id)
    if result is not None:
        status["result"] = result.to_dict()
    return status
