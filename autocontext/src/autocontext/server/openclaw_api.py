"""REST API router for OpenClaw artifact operations (AC-191)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from autocontext.config import load_settings
from autocontext.mcp.tools import MtsToolContext

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
    training_config: dict[str, Any] = Field(default_factory=dict)


class UpdateDistillJobRequest(BaseModel):
    status: str
    result_artifact_id: str | None = None
    error_message: str | None = None
    training_metrics: dict[str, Any] | None = None


# -- Endpoints --


@router.post("/evaluate")
def evaluate_strategy_endpoint(body: EvaluateRequest) -> dict[str, Any]:
    """Run tournament matches to score a candidate strategy."""
    from autocontext.mcp.tools import evaluate_strategy

    result = evaluate_strategy(body.scenario_name, body.strategy, body.num_matches, body.seed_base)
    if "error" in result:
        raise HTTPException(status_code=400, detail=str(result["error"]))
    return result


@router.post("/validate")
def validate_strategy_endpoint(
    body: ValidateRequest,
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Check a strategy against scenario constraints and harness validators."""
    from autocontext.mcp.tools import validate_strategy_against_harness

    result = validate_strategy_against_harness(body.scenario_name, body.strategy, ctx=ctx)
    if "error" in result:
        raise HTTPException(status_code=400, detail=str(result["error"]))
    return result


@router.post("/artifacts")
def publish_artifact_endpoint(
    body: dict[str, Any],
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Publish an artifact (harness, policy, or distilled model)."""
    from autocontext.mcp.tools import publish_artifact

    result = publish_artifact(ctx, body)
    if "error" in result:
        raise HTTPException(status_code=400, detail=str(result["error"]))
    return result


@router.get("/artifacts")
def list_artifacts_endpoint(
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
    scenario: str | None = None,
    artifact_type: str | None = None,
) -> list[dict[str, Any]]:
    """List published artifacts with optional filters."""
    from autocontext.mcp.tools import list_artifacts

    return list_artifacts(ctx, scenario=scenario, artifact_type=artifact_type)


@router.get("/artifacts/{artifact_id}")
def fetch_artifact_endpoint(
    artifact_id: str,
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Fetch a published artifact by its ID."""
    from autocontext.mcp.tools import fetch_artifact

    result = fetch_artifact(ctx, artifact_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=str(result["error"]))
    return result


@router.get("/distill")
def distill_status_endpoint(
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
    scenario: str | None = None,
) -> dict[str, Any]:
    """Check status of distillation workflows, optionally filtered by scenario."""
    from autocontext.mcp.tools import distill_status

    return distill_status(ctx, scenario=scenario)


@router.post("/distill")
def trigger_distillation_endpoint(
    body: TriggerDistillRequest,
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Trigger a distillation workflow for a scenario."""
    from autocontext.mcp.tools import trigger_distillation

    result = trigger_distillation(
        ctx,
        scenario=body.scenario,
        source_artifact_ids=body.source_artifact_ids,
        training_config=body.training_config or None,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=str(result["error"]))
    return result


@router.get("/distill/{job_id}")
def get_distill_job_endpoint(
    job_id: str,
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Get details of a specific distillation job."""
    from autocontext.mcp.tools import get_distill_job

    result = get_distill_job(ctx, job_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=str(result["error"]))
    return result


@router.patch("/distill/{job_id}")
def update_distill_job_endpoint(
    job_id: str,
    body: UpdateDistillJobRequest,
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Update a distillation job status (sidecar reporting endpoint)."""
    from autocontext.mcp.tools import update_distill_job

    result = update_distill_job(
        ctx, job_id, body.status,
        result_artifact_id=body.result_artifact_id,
        error_message=body.error_message,
        training_metrics=body.training_metrics,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=str(result["error"]))
    return result


@router.get("/capabilities")
def capabilities_endpoint() -> dict[str, Any]:
    """Return capability metadata for this autocontext instance."""
    from autocontext.mcp.tools import get_capabilities

    return get_capabilities()


# -- Discovery & capability advertisement (AC-195) --


@router.get("/discovery/capabilities")
def discovery_capabilities_endpoint(
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Full capability advertisement: version, runtime health, scenarios, artifacts."""
    from autocontext.mcp.tools import skill_advertise_capabilities

    return skill_advertise_capabilities(ctx)


@router.get("/discovery/scenario/{scenario_name}")
def discovery_scenario_endpoint(
    scenario_name: str,
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Per-scenario capabilities: evaluation mode, harness, playbook, best scores."""
    from autocontext.mcp.tools import skill_scenario_capabilities

    try:
        return skill_scenario_capabilities(ctx, scenario_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_name}' not found") from None


@router.get("/discovery/health")
def discovery_health_endpoint(
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Runtime health: executor mode, provider, harness mode, available models."""
    from autocontext.mcp.tools import skill_runtime_health

    return skill_runtime_health(ctx)


@router.get("/discovery/scenario/{scenario_name}/artifacts")
def discovery_scenario_artifacts_endpoint(
    scenario_name: str,
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> list[dict[str, Any]]:
    """All artifacts associated with a specific scenario."""
    from autocontext.mcp.tools import skill_scenario_artifact_lookup

    return skill_scenario_artifact_lookup(ctx, scenario_name)


@router.get("/skill/manifest")
def skill_manifest_endpoint(
    ctx: Annotated[MtsToolContext, Depends(get_openclaw_ctx)],
) -> dict[str, Any]:
    """Return the ClawHub skill manifest for this autocontext instance."""
    from autocontext.mcp.tools import skill_manifest

    return skill_manifest(ctx)
