"""Discovery and capability advertisement for ClawHub (AC-195).

Allows external clients to discover what autocontext can serve for a scenario:
scenario-to-artifact lookup, capability advertisement, runtime health,
and client-friendly summaries for ClawHub UX.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from autocontext.concepts import get_concept_model
from autocontext.scenarios.families import detect_family
from autocontext.storage.artifacts import EMPTY_PLAYBOOK_SENTINEL
from autocontext.util.json_io import read_json

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings
    from autocontext.mcp.tools import MtsToolContext

_DISCOVERY_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ArtifactSummary(BaseModel):
    """Lightweight summary of a single published artifact."""

    artifact_id: str
    name: str
    artifact_type: str
    scenario: str
    version: int = 0


class ScenarioCapabilities(BaseModel):
    """Per-scenario capability description: what operations are available."""

    scenario_name: str
    evaluation_mode: str = Field(description="'tournament' for game scenarios, 'judge' for agent tasks")
    has_harness: bool = False
    has_policy: bool = False
    has_playbook: bool = False
    harness_count: int = 0
    best_score: float | None = None
    best_elo: float | None = None


class RuntimeHealth(BaseModel):
    """Runtime status snapshot: current configuration state."""

    executor_mode: str
    agent_provider: str
    harness_mode: str
    rlm_enabled: bool
    available_models: dict[str, str] = Field(default_factory=dict)
    openclaw_runtime_kind: str | None = None
    openclaw_compatibility_version: str | None = None


class CapabilityAdvertisement(BaseModel):
    """Full capability advertisement: version, runtime, scenarios, artifacts."""

    version: str
    runtime_health: RuntimeHealth
    concept_model: dict[str, object] = Field(default_factory=dict)
    scenario_capabilities: dict[str, ScenarioCapabilities] = Field(default_factory=dict)
    artifact_counts: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_scenario_best_metrics(ctx: MtsToolContext, scenario_name: str) -> tuple[float | None, float | None]:
    """Query SQLite for the best score and best Elo across all runs for a scenario.

    Returns (best_score, best_elo). Either may be None if no data exists.
    """
    try:
        snapshot = ctx.sqlite.get_best_knowledge_snapshot(scenario_name)
        if snapshot is not None:
            best_score = snapshot.get("best_score")
            best_elo = snapshot.get("best_elo")
            return (
                float(best_score) if best_score is not None else None,
                float(best_elo) if best_elo is not None else None,
            )
    except Exception:
        logger.debug("openclaw.discovery: suppressed Exception", exc_info=True)
    return (None, None)


def _count_artifacts_by_type(knowledge_root: Path) -> dict[str, int]:
    """Count published artifacts grouped by artifact_type."""
    artifacts_dir = knowledge_root / "_openclaw_artifacts"
    if not artifacts_dir.exists():
        return {}

    counts: dict[str, int] = {}
    for path in artifacts_dir.glob("*.json"):
        try:
            data = read_json(path)
            atype = data.get("artifact_type", "")
            if atype:
                counts[atype] = counts.get(atype, 0) + 1
        except Exception:
            logger.debug("openclaw.discovery: caught Exception", exc_info=True)
            continue
    return counts


def _has_policy_artifact(knowledge_root: Path, scenario_name: str) -> bool:
    """Check whether any policy artifact exists for the given scenario."""
    artifacts_dir = knowledge_root / "_openclaw_artifacts"
    if not artifacts_dir.exists():
        return False

    for path in artifacts_dir.glob("*.json"):
        try:
            data = read_json(path)
            if data.get("artifact_type") == "policy" and data.get("scenario") == scenario_name:
                return True
        except Exception:
            logger.debug("openclaw.discovery: caught Exception", exc_info=True)
            continue
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover_scenario_capabilities(ctx: MtsToolContext, scenario_name: str) -> ScenarioCapabilities:
    """Check what operations/artifacts are available for a specific scenario.

    Raises KeyError if the scenario is not registered.
    """
    from autocontext.scenarios import SCENARIO_REGISTRY

    if scenario_name not in SCENARIO_REGISTRY:
        raise KeyError(scenario_name)

    family = detect_family(SCENARIO_REGISTRY[scenario_name]())
    if family is None:
        raise TypeError(f"Unable to determine scenario family for '{scenario_name}'")
    evaluation_mode = "judge" if family.evaluation_mode == "llm_judge" else family.evaluation_mode

    # Check playbook
    has_playbook = False
    try:
        playbook = ctx.artifacts.read_playbook(scenario_name)
        has_playbook = bool(playbook and playbook.strip() and playbook != EMPTY_PLAYBOOK_SENTINEL)
    except Exception:
        logger.debug("openclaw.discovery: suppressed Exception", exc_info=True)

    # Check harness files
    has_harness = False
    harness_count = 0
    try:
        harness_dir: Path = ctx.artifacts.harness_dir(scenario_name)
        if harness_dir.exists():
            harness_files = list(harness_dir.glob("*.py"))
            harness_count = len(harness_files)
            has_harness = harness_count > 0
    except Exception:
        logger.debug("openclaw.discovery: suppressed Exception", exc_info=True)

    # Check policy artifacts
    has_policy = _has_policy_artifact(ctx.settings.knowledge_root, scenario_name)

    # Best metrics from DB
    best_score, best_elo = _get_scenario_best_metrics(ctx, scenario_name)

    return ScenarioCapabilities(
        scenario_name=scenario_name,
        evaluation_mode=evaluation_mode,
        has_harness=has_harness,
        has_policy=has_policy,
        has_playbook=has_playbook,
        harness_count=harness_count,
        best_score=best_score,
        best_elo=best_elo,
    )


def get_runtime_health(settings: AppSettings) -> RuntimeHealth:
    """Read current configuration state and return a runtime health snapshot."""
    openclaw_runtime_kind = getattr(settings, "openclaw_runtime_kind", "").strip() or None
    openclaw_compatibility_version = getattr(settings, "openclaw_compatibility_version", "").strip() or None
    available_models = {
        "competitor": settings.model_competitor,
        "analyst": settings.model_analyst,
        "coach": settings.model_coach,
        "architect": settings.model_architect,
        "judge": settings.judge_model,
    }

    return RuntimeHealth(
        executor_mode=settings.executor_mode,
        agent_provider=settings.agent_provider,
        harness_mode=str(settings.harness_mode.value) if hasattr(settings.harness_mode, "value") else str(settings.harness_mode),
        rlm_enabled=settings.rlm_enabled,
        available_models=available_models,
        openclaw_runtime_kind=openclaw_runtime_kind,
        openclaw_compatibility_version=openclaw_compatibility_version,
    )


def advertise_capabilities(ctx: MtsToolContext) -> CapabilityAdvertisement:
    """Build a full capability advertisement: version, runtime, scenarios, artifacts."""
    from autocontext.scenarios import SCENARIO_REGISTRY

    runtime_health = get_runtime_health(ctx.settings)

    scenario_capabilities: dict[str, ScenarioCapabilities] = {}
    for scenario_name in SCENARIO_REGISTRY:
        try:
            caps = discover_scenario_capabilities(ctx, scenario_name)
            scenario_capabilities[scenario_name] = caps
        except Exception:
            logger.debug("openclaw.discovery: caught Exception", exc_info=True)
            continue

    artifact_counts = _count_artifacts_by_type(ctx.settings.knowledge_root)

    return CapabilityAdvertisement(
        version=_DISCOVERY_VERSION,
        runtime_health=runtime_health,
        concept_model=get_concept_model(),
        scenario_capabilities=scenario_capabilities,
        artifact_counts=artifact_counts,
    )


def scenario_artifact_lookup(ctx: MtsToolContext, scenario_name: str) -> list[ArtifactSummary]:
    """Return all artifacts associated with a specific scenario."""
    artifacts_dir = ctx.settings.knowledge_root / "_openclaw_artifacts"
    if not artifacts_dir.exists():
        return []

    results: list[ArtifactSummary] = []
    for path in sorted(artifacts_dir.glob("*.json")):
        try:
            data = read_json(path)
        except Exception:
            logger.debug("openclaw.discovery: caught Exception", exc_info=True)
            continue
        if data.get("scenario") != scenario_name:
            continue
        results.append(ArtifactSummary(
            artifact_id=data.get("id", path.stem),
            name=data.get("name", ""),
            artifact_type=data.get("artifact_type", ""),
            scenario=data.get("scenario", ""),
            version=data.get("version", 0),
        ))
    return results
