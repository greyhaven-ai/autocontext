"""Research hub substrate — shareable sessions, packages, and results (AC-267).

Builds the autocontext-side collaboration layer on top of the existing
notebook/package/report seams:
- SQLite stores the indexed metadata and promotion/adoption history
- filesystem artifacts store full package and result payloads
- `/api/hub` consumers can query shareable research objects directly
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from autocontext.analytics.facets import RunFacet
from autocontext.analytics.store import FacetStore
from autocontext.analytics.trace_reporter import ReportStore, TraceWriteup
from autocontext.knowledge.export import export_skill_package
from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package
from autocontext.notebook.types import SessionNotebook
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.families import detect_family
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore
from autocontext.util.json_io import read_json, write_json


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class ResearchSession:
    """Session notebook extended with ownership, status, and sharing."""

    session_id: str
    scenario_name: str
    owner: str
    status: str  # active, blocked, stale, completed
    lease_expires_at: str
    last_heartbeat_at: str
    current_objective: str
    current_hypotheses: list[str]
    best_run_id: str | None
    best_generation: int | None
    best_score: float | None
    unresolved_questions: list[str]
    operator_observations: list[str]
    follow_ups: list[str]
    shared: bool
    external_link: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "scenario_name": self.scenario_name,
            "owner": self.owner,
            "status": self.status,
            "lease_expires_at": self.lease_expires_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "current_objective": self.current_objective,
            "current_hypotheses": self.current_hypotheses,
            "best_run_id": self.best_run_id,
            "best_generation": self.best_generation,
            "best_score": self.best_score,
            "unresolved_questions": self.unresolved_questions,
            "operator_observations": self.operator_observations,
            "follow_ups": self.follow_ups,
            "shared": self.shared,
            "external_link": self.external_link,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchSession:
        return cls(
            session_id=data["session_id"],
            scenario_name=data.get("scenario_name", ""),
            owner=data.get("owner", ""),
            status=data.get("status", "active"),
            lease_expires_at=data.get("lease_expires_at", ""),
            last_heartbeat_at=data.get("last_heartbeat_at", ""),
            current_objective=data.get("current_objective", ""),
            current_hypotheses=data.get("current_hypotheses", []),
            best_run_id=data.get("best_run_id"),
            best_generation=data.get("best_generation"),
            best_score=data.get("best_score"),
            unresolved_questions=data.get("unresolved_questions", []),
            operator_observations=data.get("operator_observations", []),
            follow_ups=data.get("follow_ups", []),
            shared=data.get("shared", False),
            external_link=data.get("external_link", ""),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_notebook(
        cls,
        notebook: SessionNotebook,
        owner: str = "",
        shared: bool = False,
    ) -> ResearchSession:
        now = datetime.now(UTC).isoformat()
        return cls(
            session_id=notebook.session_id,
            scenario_name=notebook.scenario_name,
            owner=owner,
            status="active",
            lease_expires_at="",
            last_heartbeat_at=now,
            current_objective=notebook.current_objective,
            current_hypotheses=list(notebook.current_hypotheses),
            best_run_id=notebook.best_run_id,
            best_generation=notebook.best_generation,
            best_score=notebook.best_score,
            unresolved_questions=list(notebook.unresolved_questions),
            operator_observations=list(notebook.operator_observations),
            follow_ups=list(notebook.follow_ups),
            shared=shared,
        )


@dataclass(slots=True)
class SharedPackage:
    """Strategy package with provenance and evidence metadata."""

    package_id: str
    scenario_name: str
    scenario_family: str
    source_run_id: str
    source_generation: int
    title: str
    description: str
    strategy: dict[str, Any]
    provider_summary: str
    executor_summary: str
    best_score: float
    best_elo: float
    normalized_progress: str
    weakness_summary: str
    result_summary: str
    notebook_hypotheses: list[str]
    linked_artifacts: list[str]
    compatibility_tags: list[str]
    adoption_notes: str
    promotion_level: str  # experimental, recommended, stable
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "scenario_name": self.scenario_name,
            "scenario_family": self.scenario_family,
            "source_run_id": self.source_run_id,
            "source_generation": self.source_generation,
            "title": self.title,
            "description": self.description,
            "strategy": self.strategy,
            "provider_summary": self.provider_summary,
            "executor_summary": self.executor_summary,
            "best_score": self.best_score,
            "best_elo": self.best_elo,
            "normalized_progress": self.normalized_progress,
            "weakness_summary": self.weakness_summary,
            "result_summary": self.result_summary,
            "notebook_hypotheses": self.notebook_hypotheses,
            "linked_artifacts": self.linked_artifacts,
            "compatibility_tags": self.compatibility_tags,
            "adoption_notes": self.adoption_notes,
            "promotion_level": self.promotion_level,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SharedPackage:
        return cls(
            package_id=data["package_id"],
            scenario_name=data.get("scenario_name", ""),
            scenario_family=data.get("scenario_family", ""),
            source_run_id=data.get("source_run_id", ""),
            source_generation=data.get("source_generation", 0),
            title=data.get("title", ""),
            description=data.get("description", ""),
            strategy=data.get("strategy", {}),
            provider_summary=data.get("provider_summary", ""),
            executor_summary=data.get("executor_summary", ""),
            best_score=data.get("best_score", 0.0),
            best_elo=data.get("best_elo", 0.0),
            normalized_progress=data.get("normalized_progress", ""),
            weakness_summary=data.get("weakness_summary", ""),
            result_summary=data.get("result_summary", ""),
            notebook_hypotheses=data.get("notebook_hypotheses", []),
            linked_artifacts=data.get("linked_artifacts", []),
            compatibility_tags=data.get("compatibility_tags", []),
            adoption_notes=data.get("adoption_notes", ""),
            promotion_level=data.get("promotion_level", "experimental"),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class ResearchResult:
    """Materialized evidence-backed result summary."""

    result_id: str
    scenario_name: str
    run_id: str
    package_id: str | None
    title: str
    summary: str
    best_score: float
    best_elo: float
    normalized_progress: str
    cost_summary: str
    weakness_summary: str
    consultation_summary: str
    friction_signals: list[str]
    delight_signals: list[str]
    created_at: str
    tags: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "scenario_name": self.scenario_name,
            "run_id": self.run_id,
            "package_id": self.package_id,
            "title": self.title,
            "summary": self.summary,
            "best_score": self.best_score,
            "best_elo": self.best_elo,
            "normalized_progress": self.normalized_progress,
            "cost_summary": self.cost_summary,
            "weakness_summary": self.weakness_summary,
            "consultation_summary": self.consultation_summary,
            "friction_signals": self.friction_signals,
            "delight_signals": self.delight_signals,
            "created_at": self.created_at,
            "tags": self.tags,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchResult:
        return cls(
            result_id=data["result_id"],
            scenario_name=data.get("scenario_name", ""),
            run_id=data.get("run_id", ""),
            package_id=data.get("package_id"),
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            best_score=data.get("best_score", 0.0),
            best_elo=data.get("best_elo", 0.0),
            normalized_progress=data.get("normalized_progress", ""),
            cost_summary=data.get("cost_summary", ""),
            weakness_summary=data.get("weakness_summary", ""),
            consultation_summary=data.get("consultation_summary", ""),
            friction_signals=data.get("friction_signals", []),
            delight_signals=data.get("delight_signals", []),
            created_at=data.get("created_at", ""),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class PromotionEvent:
    """Records a promotion or adoption action."""

    event_id: str
    package_id: str
    source_run_id: str
    action: str  # promote, adopt, label
    actor: str
    label: str | None
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "package_id": self.package_id,
            "source_run_id": self.source_run_id,
            "action": self.action,
            "actor": self.actor,
            "label": self.label,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromotionEvent:
        return cls(
            event_id=data["event_id"],
            package_id=data.get("package_id", ""),
            source_run_id=data.get("source_run_id", ""),
            action=data.get("action", ""),
            actor=data.get("actor", ""),
            label=data.get("label"),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )


def materialize_result(
    facet: RunFacet,
    title: str = "",
    weakness_summary: str = "",
    consultation_summary: str = "",
    package_id: str | None = None,
) -> ResearchResult:
    """Create a ResearchResult from a RunFacet and optional report data."""
    now = datetime.now(UTC).isoformat()

    progress_parts = []
    if facet.advances:
        progress_parts.append(f"{facet.advances} advance(s)")
    if facet.retries:
        progress_parts.append(f"{facet.retries} retry(ies)")
    if facet.rollbacks:
        progress_parts.append(f"{facet.rollbacks} rollback(s)")
    normalized_progress = ", ".join(progress_parts) if progress_parts else "No generations"

    cost_summary = f"${facet.total_cost_usd:.2f} total, {facet.total_tokens} tokens"

    friction_strs = [s.description for s in facet.friction_signals]
    delight_strs = [s.description for s in facet.delight_signals]

    summary = (
        f"Run {facet.run_id} on {facet.scenario}: "
        f"best score {facet.best_score:.2f}, "
        f"{facet.total_generations} generation(s), "
        f"{normalized_progress}."
    )

    return ResearchResult(
        result_id=f"res-{_uid()}",
        scenario_name=facet.scenario,
        run_id=facet.run_id,
        package_id=package_id,
        title=title or f"{facet.scenario} Run {facet.run_id}",
        summary=summary,
        best_score=facet.best_score,
        best_elo=facet.best_elo,
        normalized_progress=normalized_progress,
        cost_summary=cost_summary,
        weakness_summary=weakness_summary,
        consultation_summary=consultation_summary,
        friction_signals=friction_strs,
        delight_signals=delight_strs,
        created_at=now,
        tags=[facet.scenario, facet.scenario_family, facet.agent_provider],
    )


class HubStore:
    """Research-hub service over existing SQLite + artifact storage."""

    def __init__(
        self,
        sqlite: SQLiteStore,
        artifacts: ArtifactStore,
        analytics_root: Path | None = None,
    ) -> None:
        self.sqlite = sqlite
        self.artifacts = artifacts
        self.analytics_root = analytics_root or (artifacts.knowledge_root / "analytics")
        self._facet_store = FacetStore(artifacts.knowledge_root)
        self._report_store = ReportStore(self.analytics_root)
        self._hub_root = artifacts.knowledge_root / "_hub"
        self._packages_dir = self._hub_root / "packages"
        self._results_dir = self._hub_root / "results"
        for directory in (self._packages_dir, self._results_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def _package_dir(self, package_id: str) -> Path:
        return self._packages_dir / package_id

    def _shared_package_path(self, package_id: str) -> Path:
        return self._package_dir(package_id) / "shared_package.json"

    def _strategy_package_path(self, package_id: str) -> Path:
        return self._package_dir(package_id) / "strategy_package.json"

    def _result_path(self, result_id: str) -> Path:
        return self._results_dir / f"{result_id}.json"

    def _relative_artifact_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.artifacts.knowledge_root))
        except ValueError:
            return str(path)

    @staticmethod
    def _safe_iso(created_at: str) -> str:
        return created_at or _now()

    def _compose_session(
        self,
        notebook: dict[str, Any],
        metadata: dict[str, Any] | None,
    ) -> ResearchSession:
        session = ResearchSession.from_notebook(SessionNotebook.from_dict(notebook))
        session.last_heartbeat_at = str(notebook.get("updated_at", "") or notebook.get("created_at", ""))
        if metadata is None:
            return session
        session.owner = str(metadata.get("owner", ""))
        session.status = str(metadata.get("status", "active"))
        session.lease_expires_at = str(metadata.get("lease_expires_at", ""))
        session.last_heartbeat_at = str(metadata.get("last_heartbeat_at", session.last_heartbeat_at))
        session.shared = bool(metadata.get("shared", False))
        session.external_link = str(metadata.get("external_link", ""))
        session.metadata = dict(metadata.get("metadata", {}))
        return session

    def _load_run_facet(self, run_id: str) -> RunFacet:
        facet = self._facet_store.load(run_id)
        if facet is None:
            raise ValueError(f"No persisted facet found for run {run_id}")
        return facet

    def _load_run_writeup(self, run_id: str) -> TraceWriteup | None:
        return self._report_store.latest_writeup_for_run(run_id)

    def _parse_best_strategy(self, raw_content: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            return {"raw_output": raw_content}
        return parsed if isinstance(parsed, dict) else {"raw_output": raw_content}

    def _extract_weakness_summary(self, report: object | None) -> str:
        if report is None:
            return ""
        weaknesses = getattr(report, "weaknesses", None)
        if isinstance(weaknesses, list) and weaknesses:
            parts: list[str] = []
            for weakness in weaknesses[:3]:
                title = getattr(weakness, "title", "")
                description = getattr(weakness, "description", "")
                text = str(title or description).strip()
                if text:
                    parts.append(text)
            if parts:
                return "; ".join(parts)
        to_markdown = getattr(report, "to_markdown", None)
        if callable(to_markdown):
            return str(to_markdown()).splitlines()[0].strip()
        return ""

    def _consultation_summary(self, run_id: str) -> str:
        consultations = self.sqlite.get_consultations_for_run(run_id)
        if not consultations:
            return ""
        total_cost = self.sqlite.get_total_consultation_cost(run_id)
        latest = consultations[-1]
        trigger = str(latest.get("trigger", "")).strip()
        summary = f"{len(consultations)} consultation(s), ${total_cost:.2f} total"
        if trigger:
            summary += f", latest trigger: {trigger}"
        return summary

    def _progress_summary(self, facet: RunFacet, progress_report: object | None) -> str:
        if progress_report is not None:
            pct = getattr(getattr(progress_report, "progress", None), "pct_of_ceiling", None)
            advances = getattr(progress_report, "advances", facet.advances)
            retries = getattr(progress_report, "retries", facet.retries)
            rollbacks = getattr(progress_report, "rollbacks", facet.rollbacks)
            if pct is not None:
                return (
                    f"{float(pct):.2f}% of ceiling, "
                    f"{int(advances)} advance(s), {int(retries)} retry(ies), "
                    f"{int(rollbacks)} rollback(s)"
                )
        progress_parts = []
        if facet.advances:
            progress_parts.append(f"{facet.advances} advance(s)")
        if facet.retries:
            progress_parts.append(f"{facet.retries} retry(ies)")
        if facet.rollbacks:
            progress_parts.append(f"{facet.rollbacks} rollback(s)")
        return ", ".join(progress_parts) if progress_parts else "No generations"

    def _run_linked_artifacts(self, scenario_name: str, run_id: str) -> list[str]:
        linked: list[str] = []

        session_report = self.artifacts.knowledge_root / scenario_name / "reports" / f"{run_id}.md"
        if session_report.exists():
            linked.append(self._relative_artifact_path(session_report))

        progress_report = self.artifacts.knowledge_root / scenario_name / "progress_reports" / f"{run_id}.json"
        if progress_report.exists():
            linked.append(self._relative_artifact_path(progress_report))

        weakness_report = self.artifacts.knowledge_root / scenario_name / "weakness_reports" / f"{run_id}.json"
        if weakness_report.exists():
            linked.append(self._relative_artifact_path(weakness_report))

        facet_path = self.analytics_root / "facets" / f"{run_id}.json"
        if facet_path.exists():
            linked.append(self._relative_artifact_path(facet_path))

        writeup = self._load_run_writeup(run_id)
        if writeup is not None:
            writeup_path = self.analytics_root / "writeups" / f"{writeup.writeup_id}.json"
            if writeup_path.exists():
                linked.append(self._relative_artifact_path(writeup_path))

        return linked

    def _build_strategy_package(self, run_id: str) -> tuple[StrategyPackage, dict[str, Any], dict[str, Any]]:
        run = self.sqlite.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown run: {run_id}")
        scenario_name = str(run["scenario"])
        strategy_history = self.sqlite.get_strategy_score_history(run_id)
        if not strategy_history:
            raise ValueError(f"No competitor strategy history found for run {run_id}")

        best_entry = max(
            strategy_history,
            key=lambda row: (
                float(row.get("best_score", 0.0) or 0.0),
                int(row.get("generation_index", 0) or 0),
            ),
        )
        best_strategy = self._parse_best_strategy(str(best_entry.get("content", "")))
        generation_metrics = self.sqlite.get_generation_metrics(run_id)
        best_generation = int(best_entry.get("generation_index", 0) or 0)
        elo_by_generation = {
            int(row.get("generation_index", 0) or 0): float(row.get("elo", 0.0) or 0.0)
            for row in generation_metrics
        }
        best_elo = float(elo_by_generation.get(best_generation, 0.0))

        export_ctx = cast(Any, SimpleNamespace(sqlite=self.sqlite, artifacts=self.artifacts))
        skill_pkg = export_skill_package(export_ctx, scenario_name)
        skill_pkg.best_strategy = best_strategy
        skill_pkg.best_score = float(best_entry.get("best_score", 0.0) or 0.0)
        skill_pkg.best_elo = best_elo
        if isinstance(skill_pkg.metadata, dict):
            skill_pkg.metadata["source_run_id"] = run_id
            skill_pkg.metadata["source_generation"] = best_generation

        strategy_pkg = StrategyPackage.from_skill_package(skill_pkg, source_run_id=run_id)
        strategy_pkg.best_strategy = best_strategy
        strategy_pkg.best_score = skill_pkg.best_score
        strategy_pkg.best_elo = skill_pkg.best_elo
        strategy_pkg.metadata.created_at = _now()

        return strategy_pkg, run, best_entry

    def _scenario_family(self, scenario_name: str) -> str:
        cls = SCENARIO_REGISTRY.get(scenario_name)
        if cls is None:
            return ""
        family = detect_family(cls())
        return family.name if family is not None else ""

    def _package_session(self, session_id: str | None, run_id: str) -> ResearchSession | None:
        if session_id:
            return self.load_session(session_id)
        notebooks = self.sqlite.list_notebooks_for_run(run_id)
        if not notebooks:
            return None
        return self.load_session(str(notebooks[0]["session_id"]))

    # --- Sessions ---

    def persist_session(self, session: ResearchSession) -> Path:
        self.sqlite.upsert_notebook(
            session_id=session.session_id,
            scenario_name=session.scenario_name,
            current_objective=session.current_objective,
            current_hypotheses=session.current_hypotheses,
            best_run_id=session.best_run_id,
            best_generation=session.best_generation,
            best_score=session.best_score,
            unresolved_questions=session.unresolved_questions,
            operator_observations=session.operator_observations,
            follow_ups=session.follow_ups,
        )
        self.sqlite.upsert_hub_session(
            session.session_id,
            owner=session.owner,
            status=session.status,
            lease_expires_at=session.lease_expires_at,
            last_heartbeat_at=session.last_heartbeat_at or _now(),
            shared=session.shared,
            external_link=session.external_link,
            metadata=session.metadata,
        )
        notebook = self.sqlite.get_notebook(session.session_id)
        if notebook is None:
            raise ValueError(f"Failed to persist notebook for session {session.session_id}")
        self.artifacts.write_notebook(session.session_id, notebook)
        return self.artifacts.runs_root / "sessions" / session.session_id / "notebook.json"

    def load_session(self, session_id: str) -> ResearchSession | None:
        notebook = self.sqlite.get_notebook(session_id)
        if notebook is None:
            return None
        metadata = self.sqlite.get_hub_session(session_id)
        return self._compose_session(notebook, metadata)

    def list_sessions(self) -> list[ResearchSession]:
        metadata_by_session = {
            str(row["session_id"]): row for row in self.sqlite.list_hub_sessions()
        }
        sessions: list[ResearchSession] = []
        for notebook in self.sqlite.list_notebooks():
            session_id = str(notebook["session_id"])
            sessions.append(self._compose_session(notebook, metadata_by_session.get(session_id)))
        return sessions

    def heartbeat_session(
        self,
        session_id: str,
        *,
        lease_expires_at: str = "",
    ) -> ResearchSession:
        notebook = self.sqlite.get_notebook(session_id)
        if notebook is None:
            raise ValueError(f"Notebook not found for session {session_id}")
        self.sqlite.heartbeat_hub_session(
            session_id,
            last_heartbeat_at=_now(),
            lease_expires_at=lease_expires_at or None,
        )
        metadata = self.sqlite.get_hub_session(session_id)
        return self._compose_session(notebook, metadata)

    # --- Packages ---

    def persist_package(self, package: SharedPackage, strategy_package: StrategyPackage | None = None) -> Path:
        package_dir = self._package_dir(package.package_id)
        package_dir.mkdir(parents=True, exist_ok=True)
        payload_path = self._shared_package_path(package.package_id)
        write_json(payload_path, package.to_dict())

        strategy_package_path = ""
        if strategy_package is not None:
            strategy_payload_path = self._strategy_package_path(package.package_id)
            strategy_package.to_file(strategy_payload_path)
            strategy_package_path = self._relative_artifact_path(strategy_payload_path)

        self.sqlite.save_hub_package_record(
            package_id=package.package_id,
            scenario_name=package.scenario_name,
            scenario_family=package.scenario_family,
            source_run_id=package.source_run_id,
            source_generation=package.source_generation,
            title=package.title,
            description=package.description,
            promotion_level=package.promotion_level,
            best_score=package.best_score,
            best_elo=package.best_elo,
            payload_path=self._relative_artifact_path(payload_path),
            strategy_package_path=strategy_package_path,
            tags=package.compatibility_tags,
            metadata=package.metadata,
            created_at=self._safe_iso(package.created_at),
        )
        return payload_path

    def load_package(self, package_id: str) -> SharedPackage | None:
        row = self.sqlite.get_hub_package_record(package_id)
        if row is None:
            return None
        path = self.artifacts.knowledge_root / str(row["payload_path"])
        if not path.exists():
            return None
        return SharedPackage.from_dict(read_json(path))

    def list_packages(self) -> list[SharedPackage]:
        packages: list[SharedPackage] = []
        for row in self.sqlite.list_hub_package_records():
            path = self.artifacts.knowledge_root / str(row["payload_path"])
            if not path.exists():
                continue
            packages.append(SharedPackage.from_dict(read_json(path)))
        return packages

    def load_strategy_package(self, package_id: str) -> StrategyPackage | None:
        row = self.sqlite.get_hub_package_record(package_id)
        if row is None:
            return None
        strategy_package_path = str(row.get("strategy_package_path", "")).strip()
        if not strategy_package_path:
            return None
        path = self.artifacts.knowledge_root / strategy_package_path
        if not path.exists():
            return None
        return StrategyPackage.from_file(path)

    def adopt_package(
        self,
        package_id: str,
        *,
        actor: str,
        conflict_policy: ConflictPolicy = ConflictPolicy.MERGE,
    ) -> dict[str, Any]:
        strategy_package = self.load_strategy_package(package_id)
        if strategy_package is None:
            raise ValueError(f"Strategy package payload not found for {package_id}")
        result = import_strategy_package(
            self.artifacts,
            strategy_package,
            sqlite=self.sqlite,
            conflict_policy=conflict_policy,
        )
        event = PromotionEvent(
            event_id=f"promo-{_uid()}",
            package_id=package_id,
            source_run_id=str(strategy_package.metadata.source_run_id or ""),
            action="adopt",
            actor=actor,
            label=None,
            created_at=_now(),
            metadata={"conflict_policy": conflict_policy.value},
        )
        self.persist_promotion(event)
        return {
            "import_result": result.model_dump(),
            "promotion_event": event.to_dict(),
        }

    def promote_run_to_package(
        self,
        run_id: str,
        *,
        title: str = "",
        description: str = "",
        session_id: str | None = None,
        actor: str = "system",
        promotion_level: str = "experimental",
        compatibility_tags: list[str] | None = None,
        adoption_notes: str = "",
    ) -> SharedPackage:
        strategy_package, run, best_entry = self._build_strategy_package(run_id)
        scenario_name = str(run["scenario"])
        facet = self._load_run_facet(run_id)
        session = self._package_session(session_id, run_id)
        progress_report = self.artifacts.read_progress_report(scenario_name, run_id)
        weakness_report = self.artifacts.read_weakness_report(scenario_name, run_id)
        writeup = self._load_run_writeup(run_id)
        package_id = f"pkg-{_uid()}"
        family = self._scenario_family(scenario_name) or facet.scenario_family
        default_tags = [
            scenario_name,
            family,
            str(run.get("agent_provider", "")).strip(),
            str(run.get("executor_mode", "")).strip(),
        ]
        shared_package = SharedPackage(
            package_id=package_id,
            scenario_name=scenario_name,
            scenario_family=family,
            source_run_id=run_id,
            source_generation=int(best_entry.get("generation_index", 0) or 0),
            title=title or f"{scenario_name.replace('_', ' ').title()} package from {run_id}",
            description=description or strategy_package.description,
            strategy=strategy_package.best_strategy or {},
            provider_summary=str(run.get("agent_provider", "")).strip(),
            executor_summary=str(run.get("executor_mode", "")).strip(),
            best_score=float(strategy_package.best_score),
            best_elo=float(strategy_package.best_elo),
            normalized_progress=self._progress_summary(facet, progress_report),
            weakness_summary=self._extract_weakness_summary(weakness_report),
            result_summary=(
                writeup.summary
                if writeup is not None
                else f"Best score {strategy_package.best_score:.2f} on run {run_id}"
            ),
            notebook_hypotheses=list(session.current_hypotheses) if session is not None else [],
            linked_artifacts=self._run_linked_artifacts(scenario_name, run_id),
            compatibility_tags=[tag for tag in (compatibility_tags or default_tags) if tag],
            adoption_notes=adoption_notes,
            promotion_level=promotion_level,
            created_at=_now(),
            metadata={
                "strategy_package_format_version": strategy_package.format_version,
                "source_session_id": session.session_id if session is not None else None,
            },
        )
        self.persist_package(shared_package, strategy_package)
        self.persist_promotion(
            PromotionEvent(
                event_id=f"promo-{_uid()}",
                package_id=package_id,
                source_run_id=run_id,
                action="promote",
                actor=actor,
                label=promotion_level,
                created_at=_now(),
                metadata={"source_generation": shared_package.source_generation},
            )
        )
        return shared_package

    # --- Results ---

    def persist_result(self, result: ResearchResult) -> Path:
        path = self._result_path(result.result_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, result.to_dict())
        self.sqlite.save_hub_result_record(
            result_id=result.result_id,
            scenario_name=result.scenario_name,
            run_id=result.run_id,
            package_id=result.package_id,
            title=result.title,
            best_score=result.best_score,
            best_elo=result.best_elo,
            payload_path=self._relative_artifact_path(path),
            tags=result.tags,
            metadata=result.metadata,
            created_at=self._safe_iso(result.created_at),
        )
        return path

    def load_result(self, result_id: str) -> ResearchResult | None:
        row = self.sqlite.get_hub_result_record(result_id)
        if row is None:
            return None
        path = self.artifacts.knowledge_root / str(row["payload_path"])
        if not path.exists():
            return None
        return ResearchResult.from_dict(read_json(path))

    def list_results(self) -> list[ResearchResult]:
        results: list[ResearchResult] = []
        for row in self.sqlite.list_hub_result_records():
            path = self.artifacts.knowledge_root / str(row["payload_path"])
            if not path.exists():
                continue
            results.append(ResearchResult.from_dict(read_json(path)))
        return results

    def materialize_result_for_run(
        self,
        run_id: str,
        *,
        package_id: str | None = None,
        title: str = "",
    ) -> ResearchResult:
        run = self.sqlite.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown run: {run_id}")
        scenario_name = str(run["scenario"])
        facet = self._load_run_facet(run_id)
        progress_report = self.artifacts.read_progress_report(scenario_name, run_id)
        weakness_report = self.artifacts.read_weakness_report(scenario_name, run_id)
        writeup = self._load_run_writeup(run_id)
        result = materialize_result(
            facet,
            title=title or f"{scenario_name.replace('_', ' ').title()} result for {run_id}",
            weakness_summary=self._extract_weakness_summary(weakness_report),
            consultation_summary=self._consultation_summary(run_id),
            package_id=package_id,
        )
        result.created_at = _now()
        result.normalized_progress = self._progress_summary(facet, progress_report)
        if writeup is not None:
            result.summary = writeup.summary
        linked_artifacts = self._run_linked_artifacts(scenario_name, run_id)
        result.metadata = {
            "scenario_family": facet.scenario_family,
            "agent_provider": facet.agent_provider,
            "executor_mode": facet.executor_mode,
            "linked_artifacts": linked_artifacts,
        }
        self.persist_result(result)
        return result

    # --- Promotions ---

    def persist_promotion(self, event: PromotionEvent) -> Path:
        self.sqlite.save_hub_promotion_record(
            event_id=event.event_id,
            package_id=event.package_id,
            source_run_id=event.source_run_id,
            action=event.action,
            actor=event.actor,
            label=event.label,
            metadata=event.metadata,
            created_at=self._safe_iso(event.created_at),
        )
        promotions_dir = self._hub_root / "promotions"
        promotions_dir.mkdir(parents=True, exist_ok=True)
        path = promotions_dir / f"{event.event_id}.json"
        write_json(path, event.to_dict())
        return path

    def load_promotion(self, event_id: str) -> PromotionEvent | None:
        row = self.sqlite.get_hub_promotion_record(event_id)
        if row is None:
            return None
        return PromotionEvent.from_dict(row)

    def list_promotions(self) -> list[PromotionEvent]:
        return [PromotionEvent.from_dict(row) for row in self.sqlite.list_hub_promotion_records()]
