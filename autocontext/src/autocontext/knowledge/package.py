"""Portable strategy package — versioned, self-contained knowledge bundles.

Extends the SkillPackage concept with Pydantic validation, format versioning,
and round-trip import support for AC-189.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field, model_validator

from autocontext.storage.artifacts import EMPTY_PLAYBOOK_SENTINEL
from autocontext.util.json_io import read_json

if TYPE_CHECKING:
    from autocontext.knowledge.export import SkillPackage
    from autocontext.storage.artifacts import ArtifactStore
    from autocontext.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

PACKAGE_FORMAT_VERSION = 1


class ConflictPolicy(StrEnum):
    """How to handle conflicts when importing into existing knowledge."""

    OVERWRITE = "overwrite"
    MERGE = "merge"
    SKIP = "skip"


class PackageMetadata(BaseModel):
    """Provenance and compatibility metadata for a strategy package."""

    mts_version: str = Field(default="", description="autocontext version that created this package")
    source_run_id: str | None = Field(default=None, description="Run that produced the best strategy")
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO-8601 creation timestamp",
    )
    completed_runs: int = Field(default=0, ge=0)
    has_snapshot: bool = Field(default=False)


class StrategyPackage(BaseModel):
    """Versioned, portable strategy knowledge package.

    Designed for JSON export/import with full Pydantic validation.
    Compatible with the OpenClaw artifact contract metadata patterns.
    """

    # Format versioning
    format_version: int = Field(default=PACKAGE_FORMAT_VERSION, ge=1)

    # Core identity
    scenario_name: str = Field(..., min_length=1)
    display_name: str = Field(default="")
    description: str = Field(default="")

    # Knowledge payload
    playbook: str = Field(default="")
    lessons: list[str] = Field(default_factory=list)
    best_strategy: dict[str, Any] | None = Field(default=None)
    best_score: float = Field(default=0.0)
    best_elo: float = Field(default=1500.0)
    hints: str = Field(default="")
    harness: dict[str, str] = Field(default_factory=dict)

    # Agent task fields (optional)
    task_prompt: str | None = None
    judge_rubric: str | None = None
    example_outputs: list[dict[str, Any]] | None = None
    output_format: str | None = None
    reference_context: str | None = None
    context_preparation: str | None = None
    max_rounds: int | None = None
    quality_threshold: float | None = None

    # Provenance
    metadata: PackageMetadata = Field(default_factory=PackageMetadata)

    @model_validator(mode="after")
    def _default_display_name(self) -> StrategyPackage:
        if not self.display_name:
            self.display_name = self.scenario_name.replace("_", " ").title()
        return self

    @classmethod
    def from_skill_package(
        cls, pkg: SkillPackage, source_run_id: str | None = None,
    ) -> StrategyPackage:
        """Build a StrategyPackage from an existing SkillPackage."""
        from autocontext import __version__

        raw_meta = getattr(pkg, "metadata", None) or {}
        return cls(
            format_version=PACKAGE_FORMAT_VERSION,
            scenario_name=pkg.scenario_name,
            display_name=pkg.display_name,
            description=pkg.description,
            playbook=pkg.playbook,
            lessons=pkg.lessons,
            best_strategy=pkg.best_strategy,
            best_score=pkg.best_score,
            best_elo=pkg.best_elo,
            hints=pkg.hints,
            harness=pkg.harness,
            task_prompt=pkg.task_prompt,
            judge_rubric=pkg.judge_rubric,
            example_outputs=pkg.example_outputs,
            output_format=pkg.output_format,
            reference_context=pkg.reference_context,
            context_preparation=pkg.context_preparation,
            max_rounds=pkg.max_rounds,
            quality_threshold=pkg.quality_threshold,
            metadata=PackageMetadata(
                mts_version=__version__,
                source_run_id=source_run_id,
                completed_runs=raw_meta.get("completed_runs", 0) if isinstance(raw_meta, dict) else 0,
                has_snapshot=raw_meta.get("has_snapshot", False) if isinstance(raw_meta, dict) else False,
            ),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyPackage:
        """Deserialize from a dict with validation."""
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, json_str: str) -> StrategyPackage:
        """Deserialize from a JSON string with validation."""
        return cls.model_validate_json(json_str)

    @classmethod
    def from_file(cls, path: Path) -> StrategyPackage:
        """Load and validate from a JSON file."""
        data = read_json(path)
        return cls.from_dict(data)

    def to_json(self, indent: int = 2) -> str:
        """Serialize to a formatted JSON string."""
        return self.model_dump_json(indent=indent)

    def to_file(self, path: Path) -> None:
        """Write to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n", encoding="utf-8")

    def to_skill_package(self) -> SkillPackage:
        """Convert back to a SkillPackage for interop with existing code."""
        from autocontext.knowledge.export import SkillPackage

        return SkillPackage(
            scenario_name=self.scenario_name,
            display_name=self.display_name,
            description=self.description,
            playbook=self.playbook,
            lessons=self.lessons,
            best_strategy=self.best_strategy,
            best_score=self.best_score,
            best_elo=self.best_elo,
            hints=self.hints,
            harness=self.harness,
            metadata=self.metadata.model_dump(),
            task_prompt=self.task_prompt,
            judge_rubric=self.judge_rubric,
            example_outputs=self.example_outputs,
            output_format=self.output_format,
            reference_context=self.reference_context,
            context_preparation=self.context_preparation,
            max_rounds=self.max_rounds,
            quality_threshold=self.quality_threshold,
        )


class ImportResult(BaseModel):
    """Result of importing a strategy package."""

    scenario_name: str
    playbook_written: bool = False
    hints_written: bool = False
    harness_written: list[str] = Field(default_factory=list)
    harness_skipped: list[str] = Field(default_factory=list)
    skill_written: bool = False
    snapshot_written: bool = False
    conflict_policy: str = ""


def _package_metadata_path(artifacts: ArtifactStore, scenario_name: str) -> Path:
    return artifacts.knowledge_root / scenario_name / "package_metadata.json"


def read_package_metadata(artifacts: ArtifactStore, scenario_name: str) -> dict[str, Any]:
    path = _package_metadata_path(artifacts, scenario_name)
    if not path.exists():
        return {}
    try:
        raw = read_json(path)
    except (OSError, json.JSONDecodeError):
        logger.warning("failed to read package metadata for %s", scenario_name)
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_package_metadata(artifacts: ArtifactStore, package: StrategyPackage) -> None:
    payload: dict[str, object] = {
        "format_version": package.format_version,
        "best_strategy": cast(object, package.best_strategy),
        "best_score": package.best_score,
        "best_elo": package.best_elo,
        "metadata": cast(object, package.metadata.model_dump()),
    }
    artifacts.write_json(_package_metadata_path(artifacts, package.scenario_name), payload)


def _persist_imported_snapshot(
    sqlite: SQLiteStore,
    artifacts: ArtifactStore,
    package: StrategyPackage,
    conflict_policy: ConflictPolicy,
) -> bool:
    should_restore = (
        package.best_strategy is not None
        or package.best_score != 0.0
        or package.best_elo != 1500.0
        or package.metadata.has_snapshot
    )
    if not should_restore:
        return False

    existing_snapshot = sqlite.get_best_knowledge_snapshot(package.scenario_name)
    if conflict_policy == ConflictPolicy.SKIP and existing_snapshot is not None:
        return False
    if (
        conflict_policy == ConflictPolicy.MERGE
        and existing_snapshot is not None
        and float(existing_snapshot.get("best_score", 0.0)) > package.best_score
    ):
        return False

    created_at = package.metadata.created_at.replace(":", "-")
    run_id_suffix = package.metadata.source_run_id or created_at
    run_id = f"imported-{package.scenario_name}-{run_id_suffix}"

    sqlite.create_run(run_id, package.scenario_name, 1, "import", agent_provider="package")
    sqlite.upsert_generation(
        run_id=run_id,
        generation_index=1,
        mean_score=package.best_score,
        best_score=package.best_score,
        elo=package.best_elo,
        wins=0,
        losses=0,
        gate_decision="accepted",
        status="completed",
    )
    if package.best_strategy is not None:
        sqlite.append_agent_output(
            run_id,
            1,
            "competitor",
            json.dumps(package.best_strategy, sort_keys=True),
        )
    sqlite.mark_run_completed(run_id)
    playbook_hash = hashlib.sha256(package.playbook.encode("utf-8")).hexdigest()[:16]
    sqlite.save_knowledge_snapshot(
        package.scenario_name,
        run_id,
        package.best_score,
        package.best_elo,
        playbook_hash,
        agent_provider="package",
        rlm_enabled=False,
    )
    return True


def import_strategy_package(
    artifacts: ArtifactStore,
    package: StrategyPackage,
    *,
    sqlite: SQLiteStore | None = None,
    conflict_policy: ConflictPolicy = ConflictPolicy.MERGE,
) -> ImportResult:
    """Hydrate a scenario's knowledge directory from a strategy package.

    Args:
        artifacts: ArtifactStore instance.
        package: The strategy package to import.
        conflict_policy: How to handle existing knowledge.

    Returns:
        ImportResult describing what was written/skipped.
    """
    scenario = package.scenario_name
    result = ImportResult(scenario_name=scenario, conflict_policy=conflict_policy.value)
    _write_package_metadata(artifacts, package)

    # ── Playbook ──
    if package.playbook:
        existing = artifacts.read_playbook(scenario)
        is_empty = not existing or existing == EMPTY_PLAYBOOK_SENTINEL
        if conflict_policy == ConflictPolicy.OVERWRITE or (conflict_policy == ConflictPolicy.MERGE and is_empty):
            artifacts.write_playbook(scenario, package.playbook)
            result.playbook_written = True
        elif conflict_policy == ConflictPolicy.SKIP and is_empty:
            artifacts.write_playbook(scenario, package.playbook)
            result.playbook_written = True

    # ── Hints ──
    if package.hints:
        existing_hints = artifacts.read_hints(scenario)
        if conflict_policy == ConflictPolicy.OVERWRITE:
            artifacts.write_hints(scenario, package.hints)
            result.hints_written = True
        elif conflict_policy == ConflictPolicy.MERGE:
            if existing_hints:
                merged = existing_hints.rstrip() + "\n\n" + package.hints.strip() + "\n"
                artifacts.write_hints(scenario, merged)
            else:
                artifacts.write_hints(scenario, package.hints)
            result.hints_written = True
        elif conflict_policy == ConflictPolicy.SKIP:
            if not existing_hints:
                artifacts.write_hints(scenario, package.hints)
                result.hints_written = True

    # ── Harness validators ──
    for name, source in package.harness.items():
        existing_harness = artifacts.read_harness(scenario, name)
        if conflict_policy == ConflictPolicy.OVERWRITE:
            artifacts.write_harness(scenario, name, source)
            result.harness_written.append(name)
        elif existing_harness is not None:
            result.harness_skipped.append(name)
        else:
            artifacts.write_harness(scenario, name, source)
            result.harness_written.append(name)

    # ── SKILL.md ──
    skill_dir = artifacts.skills_root / f"{scenario.replace('_', '-')}-ops"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_pkg = package.to_skill_package()
    skill_md = skill_pkg.to_skill_markdown()
    if conflict_policy == ConflictPolicy.OVERWRITE or not skill_path.exists():
        skill_path.write_text(skill_md, encoding="utf-8")
        result.skill_written = True
    elif conflict_policy == ConflictPolicy.MERGE:
        merged_lessons = artifacts.read_skill_lessons_raw(scenario)
        for lesson in package.lessons:
            bullet = lesson if lesson.startswith("- ") else f"- {lesson}"
            if bullet not in merged_lessons:
                merged_lessons.append(bullet)
        if merged_lessons:
            artifacts.replace_skill_lessons(scenario, merged_lessons)
            result.skill_written = True

    if sqlite is not None:
        result.snapshot_written = _persist_imported_snapshot(
            sqlite,
            artifacts,
            package,
            conflict_policy,
        )

    # ── Sync to .claude/skills ──
    artifacts.sync_skills_to_claude()

    return result
