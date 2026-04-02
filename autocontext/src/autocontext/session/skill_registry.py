"""Skill manifest parsing, registry, and lazy loading (AC-509).

Domain concepts:
- SkillManifest: lightweight metadata parsed from SKILL.md frontmatter
- SkillEntry: manifest + lazy-loaded body
- SkillRegistry: discovery, dedup, search, validation
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _normalize_frontmatter_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse YAML-like frontmatter from SKILL.md (key: value lines)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = _normalize_frontmatter_value(value)
    return result


def _body_after_frontmatter(text: str) -> str:
    """Return text content after the frontmatter block."""
    match = _FRONTMATTER_RE.match(text)
    if match:
        return text[match.end():].strip()
    return text.strip()


# ---- Value Objects ----


class SkillManifest(BaseModel):
    """Lightweight metadata parsed from SKILL.md frontmatter.

    Does NOT include the full body — that's loaded lazily via SkillEntry.
    """

    name: str
    description: str = ""
    skill_path: Path = Path()
    when_to_use: str = ""
    allowed_tools: str = ""
    model_hint: str = ""

    @classmethod
    def from_skill_dir(cls, skill_dir: Path) -> SkillManifest | None:
        """Parse manifest from a skill directory containing SKILL.md.

        Returns None if SKILL.md doesn't exist.
        Falls back to directory name for missing fields.
        """
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        text = skill_md.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)

        return cls(
            name=fm.get("name", skill_dir.name),
            description=fm.get("description", ""),
            skill_path=skill_dir,
            when_to_use=fm.get("when-to-use", fm.get("when_to_use", "")),
            allowed_tools=fm.get("allowed-tools", fm.get("allowed_tools", "")),
            model_hint=fm.get("model", ""),
        )

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


# ---- Entity ----


class SkillEntry:
    """Wraps a manifest with lazy-loaded body content.

    Body is not read from disk until load_body() is called.
    """

    def __init__(self, manifest: SkillManifest) -> None:
        self.manifest = manifest
        self._body: str | None = None

    @property
    def is_loaded(self) -> bool:
        return self._body is not None

    def load_body(self) -> str:
        """Load full skill body from SKILL.md (after frontmatter). Cached."""
        if self._body is not None:
            return self._body
        skill_md = self.manifest.skill_path / "SKILL.md"
        if not skill_md.exists():
            self._body = ""
            return ""
        text = skill_md.read_text(encoding="utf-8")
        self._body = _body_after_frontmatter(text)
        return self._body


# ---- Aggregate ----


class SkillValidationError(BaseModel):
    """A validation issue with a discovered skill."""

    skill_name: str
    issue: str
    severity: str = "warning"  # warning, error

    model_config = {"frozen": True}


class SkillRegistry:
    """Discovers, deduplicates, and manages runtime skills.

    Skills are identified by name. Duplicate names from different roots
    are collapsed (first discovered wins).
    """

    def __init__(self) -> None:
        self._entries: dict[str, SkillEntry] = {}

    def discover(self, root: Path) -> int:
        """Scan a directory for skill subdirectories containing SKILL.md.

        Returns count of newly registered skills.
        """
        if not root.is_dir():
            return 0

        added = 0
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            manifest = SkillManifest.from_skill_dir(child)
            if manifest is None:
                continue
            if manifest.name not in self._entries:
                self._entries[manifest.name] = SkillEntry(manifest=manifest)
                added += 1
            else:
                logger.debug("skill '%s' already registered, skipping duplicate from %s", manifest.name, child)

        return added

    def all_manifests(self) -> list[SkillManifest]:
        """Return all registered skill manifests (lightweight, no body)."""
        return [e.manifest for e in self._entries.values()]

    def get(self, name: str) -> SkillEntry | None:
        """Look up a skill by name."""
        return self._entries.get(name)

    def search(self, query: str) -> list[SkillManifest]:
        """Search skills by keyword in name and description."""
        query_lower = query.lower()
        return [
            e.manifest for e in self._entries.values()
            if query_lower in e.manifest.name.lower()
            or query_lower in e.manifest.description.lower()
        ]

    def validate(self) -> list[SkillValidationError]:
        """Validate all registered skills. Returns list of issues."""
        errors: list[SkillValidationError] = []
        for name, entry in self._entries.items():
            if not entry.manifest.description:
                errors.append(SkillValidationError(
                    skill_name=name,
                    issue="missing description in frontmatter",
                ))
            body = entry.load_body()
            if len(body.strip()) < 10:
                errors.append(SkillValidationError(
                    skill_name=name,
                    issue="skill body is empty or too short",
                ))
        return errors
