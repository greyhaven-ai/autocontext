"""Read-only inspection of Hermes Agent v0.12 skill and curator state."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_USAGE_FILENAME = ".usage.json"
_BUNDLED_MANIFEST_FILENAME = ".bundled_manifest"
_HUB_LOCK_PATH = Path(".hub") / "lock.json"


@dataclass(frozen=True, slots=True)
class HermesSkill:
    """A skill discovered in a Hermes skills tree."""

    name: str
    path: Path
    description: str
    provenance: str
    state: str
    pinned: bool
    use_count: int
    view_count: int
    patch_count: int
    created_at: str | None
    last_used_at: str | None
    last_viewed_at: str | None
    last_patched_at: str | None
    archived_at: str | None

    @property
    def agent_created(self) -> bool:
        return self.provenance == "agent-created"

    @property
    def activity_count(self) -> int:
        return self.use_count + self.view_count + self.patch_count

    @property
    def last_activity_at(self) -> str | None:
        return _latest_activity_at(
            self.last_used_at,
            self.last_viewed_at,
            self.last_patched_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "description": self.description,
            "provenance": self.provenance,
            "agent_created": self.agent_created,
            "state": self.state,
            "pinned": self.pinned,
            "use_count": self.use_count,
            "view_count": self.view_count,
            "patch_count": self.patch_count,
            "activity_count": self.activity_count,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "last_viewed_at": self.last_viewed_at,
            "last_patched_at": self.last_patched_at,
            "last_activity_at": self.last_activity_at,
            "archived_at": self.archived_at,
        }


@dataclass(frozen=True, slots=True)
class CuratorRunSummary:
    """A compact summary of one Hermes Curator run.json report."""

    path: Path
    report_path: Path | None
    started_at: str | None
    duration_seconds: float | None
    provider: str | None
    model: str | None
    counts: dict[str, Any] = field(default_factory=dict)
    auto_transitions: dict[str, Any] = field(default_factory=dict)
    tool_call_counts: dict[str, Any] = field(default_factory=dict)
    consolidated_count: int = 0
    pruned_count: int = 0
    archived_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "report_path": str(self.report_path) if self.report_path is not None else None,
            "started_at": self.started_at,
            "duration_seconds": self.duration_seconds,
            "provider": self.provider,
            "model": self.model,
            "counts": dict(self.counts),
            "auto_transitions": dict(self.auto_transitions),
            "tool_call_counts": dict(self.tool_call_counts),
            "consolidated_count": self.consolidated_count,
            "pruned_count": self.pruned_count,
            "archived_count": self.archived_count,
        }


@dataclass(frozen=True, slots=True)
class CuratorInventory:
    """Read-only view of Hermes Curator report artifacts."""

    reports_root: Path
    run_count: int
    latest: CuratorRunSummary | None
    runs: tuple[CuratorRunSummary, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reports_root": str(self.reports_root),
            "run_count": self.run_count,
            "latest": self.latest.to_dict() if self.latest is not None else None,
            "runs": [run.to_dict() for run in self.runs],
        }


@dataclass(frozen=True, slots=True)
class HermesInventory:
    """Read-only inventory of a Hermes home directory."""

    hermes_home: Path
    skills_root: Path
    skill_count: int
    agent_created_skill_count: int
    bundled_skill_count: int
    hub_skill_count: int
    pinned_skill_count: int
    archived_skill_count: int
    usage_path: Path
    bundled_manifest_path: Path
    hub_lock_path: Path
    skills: tuple[HermesSkill, ...]
    curator: CuratorInventory

    @property
    def skills_by_name(self) -> dict[str, HermesSkill]:
        return {skill.name: skill for skill in self.skills}

    def to_dict(self) -> dict[str, Any]:
        return {
            "hermes_home": str(self.hermes_home),
            "skills_root": str(self.skills_root),
            "skill_count": self.skill_count,
            "agent_created_skill_count": self.agent_created_skill_count,
            "bundled_skill_count": self.bundled_skill_count,
            "hub_skill_count": self.hub_skill_count,
            "pinned_skill_count": self.pinned_skill_count,
            "archived_skill_count": self.archived_skill_count,
            "usage_path": str(self.usage_path),
            "bundled_manifest_path": str(self.bundled_manifest_path),
            "hub_lock_path": str(self.hub_lock_path),
            "skills": [skill.to_dict() for skill in self.skills],
            "curator": self.curator.to_dict(),
        }


def inspect_hermes_home(hermes_home: str | Path | None = None) -> HermesInventory:
    """Inspect Hermes Agent skill/curator state without mutating it."""

    home = _resolve_hermes_home(hermes_home)
    skills_root = home / "skills"
    usage_path = skills_root / _USAGE_FILENAME
    bundled_manifest_path = skills_root / _BUNDLED_MANIFEST_FILENAME
    hub_lock_path = skills_root / _HUB_LOCK_PATH

    usage = _read_usage(usage_path)
    bundled_names = _read_bundled_manifest_names(bundled_manifest_path)
    hub_names = _read_hub_installed_names(hub_lock_path)
    skills = tuple(
        sorted(
            (
                _skill_from_path(
                    skill_md,
                    skills_root=skills_root,
                    usage=usage,
                    bundled_names=bundled_names,
                    hub_names=hub_names,
                )
                for skill_md in _iter_active_skill_files(skills_root)
            ),
            key=lambda skill: skill.name,
        )
    )
    archived_count = sum(1 for _ in _iter_archived_skill_files(skills_root))

    return HermesInventory(
        hermes_home=home,
        skills_root=skills_root,
        skill_count=len(skills),
        agent_created_skill_count=sum(1 for skill in skills if skill.agent_created),
        bundled_skill_count=sum(1 for skill in skills if skill.provenance == "bundled"),
        hub_skill_count=sum(1 for skill in skills if skill.provenance == "hub"),
        pinned_skill_count=sum(1 for skill in skills if skill.pinned),
        archived_skill_count=archived_count,
        usage_path=usage_path,
        bundled_manifest_path=bundled_manifest_path,
        hub_lock_path=hub_lock_path,
        skills=skills,
        curator=_inspect_curator(home / "logs" / "curator"),
    )


def _resolve_hermes_home(hermes_home: str | Path | None) -> Path:
    if hermes_home is not None:
        return Path(hermes_home).expanduser()
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".hermes"


def _iter_active_skill_files(skills_root: Path) -> tuple[Path, ...]:
    if not skills_root.exists():
        return ()
    files: list[Path] = []
    for skill_md in skills_root.rglob("SKILL.md"):
        try:
            rel = skill_md.relative_to(skills_root)
        except ValueError:
            continue
        if not rel.parts:
            continue
        first = rel.parts[0]
        if first.startswith(".") or first == "node_modules":
            continue
        files.append(skill_md)
    return tuple(files)


def _iter_archived_skill_files(skills_root: Path) -> tuple[Path, ...]:
    archive_root = skills_root / ".archive"
    if not archive_root.exists():
        return ()
    return tuple(archive_root.rglob("SKILL.md"))


def _skill_from_path(
    skill_md: Path,
    *,
    skills_root: Path,
    usage: dict[str, dict[str, Any]],
    bundled_names: set[str],
    hub_names: set[str],
) -> HermesSkill:
    frontmatter = _read_skill_frontmatter(skill_md)
    name = _as_str(frontmatter.get("name")) or skill_md.parent.name
    description = _as_str(frontmatter.get("description")) or ""
    record = usage.get(name, {})
    provenance = _provenance(name, bundled_names=bundled_names, hub_names=hub_names)
    return HermesSkill(
        name=name,
        path=skill_md.parent.relative_to(skills_root) if _is_relative_to(skill_md.parent, skills_root) else skill_md.parent,
        description=description,
        provenance=provenance,
        state=_as_str(record.get("state")) or "active",
        pinned=bool(record.get("pinned", False)),
        use_count=_as_int(record.get("use_count")),
        view_count=_as_int(record.get("view_count")),
        patch_count=_as_int(record.get("patch_count")),
        created_at=_as_str(record.get("created_at")),
        last_used_at=_as_str(record.get("last_used_at")),
        last_viewed_at=_as_str(record.get("last_viewed_at")),
        last_patched_at=_as_str(record.get("last_patched_at")),
        archived_at=_as_str(record.get("archived_at")),
    )


def _read_skill_frontmatter(skill_md: Path) -> dict[str, Any]:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}
    frontmatter: dict[str, Any] = {}
    for line in parts[0].removeprefix("---\n").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if key in {"name", "description"}:
            frontmatter[key] = value.strip().strip("\"'")
    return frontmatter


def _read_usage(path: Path) -> dict[str, dict[str, Any]]:
    data = _read_json_object(path)
    clean: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            clean[str(key)] = value
    return clean


def _read_bundled_manifest_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    names: set[str] = set()
    for line in lines:
        name = line.strip().split(":", 1)[0].strip()
        if name:
            names.add(name)
    return names


def _read_hub_installed_names(path: Path) -> set[str]:
    data = _read_json_object(path)
    installed = data.get("installed")
    if isinstance(installed, dict):
        return {str(name) for name in installed}
    return set()


def _inspect_curator(reports_root: Path) -> CuratorInventory:
    runs = tuple(sorted((_curator_run_from_path(path) for path in reports_root.rglob("run.json")), key=_curator_sort_key))
    latest = runs[-1] if runs else None
    return CuratorInventory(
        reports_root=reports_root,
        run_count=len(runs),
        latest=latest,
        runs=runs,
    )


def _curator_run_from_path(path: Path) -> CuratorRunSummary:
    data = _read_json_object(path)
    report_path = path.with_name("REPORT.md")
    consolidated = data.get("consolidated")
    pruned = data.get("pruned")
    archived = data.get("archived")
    return CuratorRunSummary(
        path=path,
        report_path=report_path if report_path.exists() else None,
        started_at=_as_str(data.get("started_at")),
        duration_seconds=_as_float(data.get("duration_seconds")),
        provider=_as_str(data.get("provider")),
        model=_as_str(data.get("model")),
        counts=_as_dict(data.get("counts")),
        auto_transitions=_as_dict(data.get("auto_transitions")),
        tool_call_counts=_as_dict(data.get("tool_call_counts")),
        consolidated_count=len(consolidated) if isinstance(consolidated, list) else 0,
        pruned_count=len(pruned) if isinstance(pruned, list) else 0,
        archived_count=len(archived) if isinstance(archived, list) else 0,
    )


def _curator_sort_key(run: CuratorRunSummary) -> tuple[datetime, str]:
    parsed = _parse_iso_datetime(run.started_at)
    if parsed is None:
        parsed = (
            datetime.fromtimestamp(run.path.stat().st_mtime, tz=UTC)
            if run.path.exists()
            else datetime.min.replace(tzinfo=UTC)
        )
    return parsed, str(run.path)


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _provenance(name: str, *, bundled_names: set[str], hub_names: set[str]) -> str:
    if name in bundled_names:
        return "bundled"
    if name in hub_names:
        return "hub"
    return "agent-created"


def _latest_activity_at(*values: str | None) -> str | None:
    latest_dt: datetime | None = None
    latest_raw: str | None = None
    for value in values:
        parsed = _parse_iso_datetime(value)
        if parsed is None:
            continue
        if latest_dt is None or parsed > latest_dt:
            latest_dt = parsed
            latest_raw = value
    return latest_raw


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
