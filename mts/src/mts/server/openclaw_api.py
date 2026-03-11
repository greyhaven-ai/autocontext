"""REST API router for OpenClaw artifact operations (MTS-191)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from mts.config import load_settings
from mts.mcp.tools import MtsToolContext

router = APIRouter(prefix="/api/openclaw", tags=["openclaw"])


def get_openclaw_ctx(request: Request) -> MtsToolContext:
    """Resolve the OpenClaw tool context from app state instead of module globals."""
    ctx = getattr(request.app.state, "openclaw_ctx", None)
    if ctx is None:
        settings = getattr(request.app.state, "app_settings", None)
        if settings is None:
            settings = load_settings()
            request.app.state.app_settings = settings
        ctx = MtsToolContext(settings)
        request.app.state.openclaw_ctx = ctx
    return ctx


# -- Request models --


class EvaluateRequest(BaseModel):
    scenario_name: str
    strategy: dict[str, Any]
    num_matches: int = Field(default=3, ge=1, le=100)
    seed_base: int = Field(default=42)


class ValidateRequest(BaseModel):
    scenario_name: str
    strategy: dict[str, Any]


class TriggerDistillRequest(BaseModel):
    scenario: str
    source_artifact_ids: list[str] = Field(default_factory=list)


# -- Endpoints --


@router.post("/evaluate")
def evaluate_strategy_endpoint(body: EvaluateRequest) -> dict[str, Any]:
    """Run tournament matches to score a candidate strategy."""
    from mts.mcp.tools import evaluate_strategy

    result = evaluate_strategy(body.scenario_name, body.strategy, body.num_matches, body.seed_base)
    if "error" in result:
        raise HTTPException(status_code=400, detail=str(result["error"]))
    return result  # type: ignore[return-value]


@router.post("/validate")
def validate_strategy_endpoint(
    body: ValidateRequest,
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Check a strategy against scenario constraints and harness validators."""
    from mts.mcp.tools import validate_strategy_against_harness

    result = validate_strategy_against_harness(body.scenario_name, body.strategy, ctx=ctx)
    if "error" in result:
        raise HTTPException(status_code=400, detail=str(result["error"]))
    return result  # type: ignore[return-value]


@router.post("/artifacts")
def publish_artifact_endpoint(
    body: dict[str, Any],
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Publish an artifact (harness, policy, or distilled model)."""
    from mts.mcp.tools import publish_artifact

    result = publish_artifact(ctx, body)
    if "error" in result:
        raise HTTPException(status_code=400, detail=str(result["error"]))
    return result  # type: ignore[return-value]


@router.get("/artifacts")
def list_artifacts_endpoint(
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
    scenario: str | None = None,
    artifact_type: str | None = None,
) -> list[dict[str, Any]]:
    """List published artifacts with optional filters."""
    from mts.mcp.tools import list_artifacts

    return list_artifacts(ctx, scenario=scenario, artifact_type=artifact_type)  # type: ignore[return-value]


@router.get("/artifacts/{artifact_id}")
def fetch_artifact_endpoint(
    artifact_id: str,
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Fetch a published artifact by its ID."""
    from mts.mcp.tools import fetch_artifact

    result = fetch_artifact(ctx, artifact_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=str(result["error"]))
    return result  # type: ignore[return-value]


@router.get("/distill")
def distill_status_endpoint(
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Check status of distillation workflows."""
    from mts.mcp.tools import distill_status

    return distill_status(ctx)  # type: ignore[return-value]


@router.post("/distill")
def trigger_distillation_endpoint(
    body: TriggerDistillRequest,
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Trigger a distillation workflow for a scenario."""
    from mts.mcp.tools import trigger_distillation

    return trigger_distillation(  # type: ignore[return-value]
        ctx,
        scenario=body.scenario,
        source_artifact_ids=body.source_artifact_ids,
    )


@router.get("/capabilities")
def capabilities_endpoint() -> dict[str, Any]:
    """Return capability metadata for this MTS instance."""
    from mts.mcp.tools import get_capabilities

    return get_capabilities()  # type: ignore[return-value]
