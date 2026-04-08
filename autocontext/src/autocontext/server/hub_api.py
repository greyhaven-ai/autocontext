"""REST API router for the research hub collaboration layer (AC-267)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from autocontext.config import load_settings
from autocontext.knowledge.package import ConflictPolicy
from autocontext.knowledge.research_hub import HubStore, PromotionEvent, ResearchSession
from autocontext.storage import ArtifactStore, SQLiteStore, artifact_store_from_settings

hub_router = APIRouter(prefix="/api/hub", tags=["hub"])


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _get_store(request: Request) -> SQLiteStore:
    store = getattr(request.app.state, "store", None)
    if store is not None:
        return store  # type: ignore[no-any-return]
    settings = getattr(request.app.state, "app_settings", None) or load_settings()
    return SQLiteStore(settings.db_path)


def _get_artifacts(request: Request) -> ArtifactStore:
    settings = getattr(request.app.state, "app_settings", None) or load_settings()
    return artifact_store_from_settings(settings)


def _get_hub(request: Request) -> HubStore:
    settings = getattr(request.app.state, "app_settings", None) or load_settings()
    return HubStore(
        sqlite=_get_store(request),
        artifacts=_get_artifacts(request),
        analytics_root=settings.knowledge_root / "analytics",
    )


class HubSessionBody(BaseModel):
    scenario_name: str | None = None
    current_objective: str | None = None
    current_hypotheses: list[str] | None = None
    best_run_id: str | None = None
    best_generation: int | None = None
    best_score: float | None = None
    unresolved_questions: list[str] | None = None
    operator_observations: list[str] | None = None
    follow_ups: list[str] | None = None
    owner: str | None = None
    status: str | None = None
    lease_expires_at: str | None = None
    shared: bool | None = None
    external_link: str | None = None
    metadata: dict[str, Any] | None = None


class HeartbeatBody(BaseModel):
    lease_seconds: int | None = Field(default=None, ge=0)
    lease_expires_at: str | None = None


class PromoteRunBody(BaseModel):
    title: str = ""
    description: str = ""
    session_id: str | None = None
    actor: str = "system"
    promotion_level: str = "experimental"
    compatibility_tags: list[str] | None = None
    adoption_notes: str = ""


class MaterializeResultBody(BaseModel):
    package_id: str | None = None
    title: str = ""


class AdoptPackageBody(BaseModel):
    actor: str = "system"
    conflict_policy: str = Field(default="merge", pattern="^(overwrite|merge|skip)$")


class PromotionBody(BaseModel):
    package_id: str
    source_run_id: str
    action: str
    actor: str
    label: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _merge_session(session_id: str, body: HubSessionBody, existing: ResearchSession | None) -> ResearchSession:
    scenario_name = body.scenario_name or (existing.scenario_name if existing is not None else "")
    if not scenario_name:
        raise HTTPException(status_code=400, detail="scenario_name is required when creating a hub session")

    return ResearchSession(
        session_id=session_id,
        scenario_name=scenario_name,
        owner=body.owner if body.owner is not None else (existing.owner if existing is not None else ""),
        status=body.status if body.status is not None else (existing.status if existing is not None else "active"),
        lease_expires_at=body.lease_expires_at if body.lease_expires_at is not None else (
            existing.lease_expires_at if existing is not None else ""
        ),
        last_heartbeat_at=existing.last_heartbeat_at if existing is not None else _now(),
        current_objective=body.current_objective if body.current_objective is not None else (
            existing.current_objective if existing is not None else ""
        ),
        current_hypotheses=body.current_hypotheses if body.current_hypotheses is not None else (
            list(existing.current_hypotheses) if existing is not None else []
        ),
        best_run_id=body.best_run_id if body.best_run_id is not None else (
            existing.best_run_id if existing is not None else None
        ),
        best_generation=body.best_generation if body.best_generation is not None else (
            existing.best_generation if existing is not None else None
        ),
        best_score=body.best_score if body.best_score is not None else (
            existing.best_score if existing is not None else None
        ),
        unresolved_questions=body.unresolved_questions if body.unresolved_questions is not None else (
            list(existing.unresolved_questions) if existing is not None else []
        ),
        operator_observations=body.operator_observations if body.operator_observations is not None else (
            list(existing.operator_observations) if existing is not None else []
        ),
        follow_ups=body.follow_ups if body.follow_ups is not None else (
            list(existing.follow_ups) if existing is not None else []
        ),
        shared=body.shared if body.shared is not None else (existing.shared if existing is not None else False),
        external_link=body.external_link if body.external_link is not None else (
            existing.external_link if existing is not None else ""
        ),
        metadata=body.metadata if body.metadata is not None else (
            dict(existing.metadata) if existing is not None else {}
        ),
    )


@hub_router.get("/sessions")
def list_sessions(request: Request) -> list[dict[str, Any]]:
    hub = _get_hub(request)
    return [session.to_dict() for session in hub.list_sessions()]


@hub_router.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request) -> dict[str, Any]:
    hub = _get_hub(request)
    session = hub.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Hub session not found: {session_id}")
    return session.to_dict()


@hub_router.put("/sessions/{session_id}")
def upsert_session(session_id: str, body: HubSessionBody, request: Request) -> dict[str, Any]:
    hub = _get_hub(request)
    session = _merge_session(session_id, body, hub.load_session(session_id))
    persisted = hub.persist_session(session)
    result = hub.load_session(session_id)
    payload = result.to_dict() if result is not None else session.to_dict()
    payload["artifact_path"] = str(persisted)
    return payload


@hub_router.post("/sessions/{session_id}/heartbeat")
def heartbeat_session(session_id: str, body: HeartbeatBody, request: Request) -> dict[str, Any]:
    hub = _get_hub(request)
    lease_expires_at = body.lease_expires_at or ""
    if body.lease_seconds is not None:
        lease_expires_at = (datetime.now(UTC) + timedelta(seconds=body.lease_seconds)).isoformat()
    try:
        session = hub.heartbeat_session(session_id, lease_expires_at=lease_expires_at)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return session.to_dict()


@hub_router.post("/packages/from-run/{run_id}")
def promote_package_from_run(run_id: str, body: PromoteRunBody, request: Request) -> dict[str, Any]:
    hub = _get_hub(request)
    try:
        package = hub.promote_run_to_package(
            run_id,
            title=body.title,
            description=body.description,
            session_id=body.session_id,
            actor=body.actor,
            promotion_level=body.promotion_level,
            compatibility_tags=body.compatibility_tags,
            adoption_notes=body.adoption_notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return package.to_dict()


@hub_router.get("/packages")
def list_packages(request: Request) -> list[dict[str, Any]]:
    hub = _get_hub(request)
    return [package.to_dict() for package in hub.list_packages()]


@hub_router.get("/packages/{package_id}")
def get_package(package_id: str, request: Request) -> dict[str, Any]:
    hub = _get_hub(request)
    package = hub.load_package(package_id)
    if package is None:
        raise HTTPException(status_code=404, detail=f"Hub package not found: {package_id}")
    return package.to_dict()


@hub_router.post("/packages/{package_id}/adopt")
def adopt_package(package_id: str, body: AdoptPackageBody, request: Request) -> dict[str, Any]:
    hub = _get_hub(request)
    try:
        return hub.adopt_package(
            package_id,
            actor=body.actor,
            conflict_policy=ConflictPolicy(body.conflict_policy),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@hub_router.post("/results/from-run/{run_id}")
def materialize_result_from_run(run_id: str, body: MaterializeResultBody, request: Request) -> dict[str, Any]:
    hub = _get_hub(request)
    try:
        result = hub.materialize_result_for_run(
            run_id,
            package_id=body.package_id,
            title=body.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result.to_dict()


@hub_router.get("/results")
def list_results(request: Request) -> list[dict[str, Any]]:
    hub = _get_hub(request)
    return [result.to_dict() for result in hub.list_results()]


@hub_router.get("/results/{result_id}")
def get_result(result_id: str, request: Request) -> dict[str, Any]:
    hub = _get_hub(request)
    result = hub.load_result(result_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Hub result not found: {result_id}")
    return result.to_dict()


@hub_router.post("/promotions")
def create_promotion(body: PromotionBody, request: Request) -> dict[str, Any]:
    hub = _get_hub(request)
    event = PromotionEvent(
        event_id=f"promo-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
        package_id=body.package_id,
        source_run_id=body.source_run_id,
        action=body.action,
        actor=body.actor,
        label=body.label,
        created_at=_now(),
        metadata=body.metadata,
    )
    hub.persist_promotion(event)
    return event.to_dict()


@hub_router.get("/feed")
def get_feed(request: Request) -> dict[str, Any]:
    hub = _get_hub(request)
    sessions = [session.to_dict() for session in hub.list_sessions()[:5]]
    packages = [package.to_dict() for package in hub.list_packages()[:5]]
    results = [result.to_dict() for result in hub.list_results()[:5]]
    promotions = [promotion.to_dict() for promotion in hub.list_promotions()[:10]]
    return {
        "sessions": sessions,
        "packages": packages,
        "results": results,
        "promotions": promotions,
    }
