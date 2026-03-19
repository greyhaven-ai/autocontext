"""Hint volume control with impact ranking and rotation (AC-340).

Caps competitor hints at N, ranks by impact, rotates lowest-ranked
when cap is exceeded. Archived hints preserved for potential recall.

Key types:
- RankedHint: hint text with rank, generation, impact score
- HintVolumePolicy: max_hints, archive_rotated
- HintManager: add/rank/rotate/format hints with volume control
- apply_volume_cap(): simple cap for hint string lists
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def _normalize_hint_text(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"^(?:[-*]\s+|\d+\.\s+)", "", stripped)
    return stripped.strip()


def split_hint_text(hints: str) -> list[str]:
    """Parse a markdown-ish hint block into individual normalized hint lines."""
    parsed: list[str] = []
    for raw_line in hints.splitlines():
        cleaned = _normalize_hint_text(raw_line)
        if cleaned:
            parsed.append(cleaned)
    return parsed


@dataclass(slots=True)
class RankedHint:
    """A hint with impact ranking metadata."""

    text: str
    rank: int
    generation_added: int
    impact_score: float  # 0.0-1.0, higher = more effective
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "rank": self.rank,
            "generation_added": self.generation_added,
            "impact_score": self.impact_score,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RankedHint:
        return cls(
            text=data.get("text", ""),
            rank=data.get("rank", 0),
            generation_added=data.get("generation_added", 0),
            impact_score=data.get("impact_score", 0.0),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class HintVolumePolicy:
    """Configuration for hint volume control."""

    max_hints: int = 7
    archive_rotated: bool = True


class HintManager:
    """Manages ranked hints with volume control."""

    def __init__(
        self,
        policy: HintVolumePolicy,
        *,
        active: list[RankedHint] | None = None,
        archived: list[RankedHint] | None = None,
    ) -> None:
        self._policy = policy
        self._active: list[RankedHint] = list(active or [])
        self._archived: list[RankedHint] = list(archived or [])
        self._reassign_ranks()

    def add(
        self,
        text: str,
        generation: int,
        impact_score: float = 0.5,
    ) -> None:
        """Add a hint, rotating out the lowest-ranked if at capacity."""
        normalized = _normalize_hint_text(text)
        if not normalized:
            return

        existing = self._find_hint(normalized, self._active)
        if existing is not None:
            existing.generation_added = generation
            existing.impact_score = max(existing.impact_score, impact_score)
            self._reassign_ranks()
            return

        archived = self._find_hint(normalized, self._archived)
        if archived is not None:
            self._archived.remove(archived)
            archived.generation_added = generation
            archived.impact_score = max(archived.impact_score, impact_score)
            self._active.append(archived)
            self._reassign_ranks()
            self._enforce_cap()
            return

        hint = RankedHint(
            text=normalized,
            rank=len(self._active) + 1,
            generation_added=generation,
            impact_score=impact_score,
        )
        self._active.append(hint)
        self._reassign_ranks()
        self._enforce_cap()

    def add_many(
        self,
        texts: list[str],
        *,
        generation: int,
        impact_score: float = 0.5,
    ) -> None:
        for text in texts:
            self.add(text, generation=generation, impact_score=impact_score)

    def merge_hint_text(
        self,
        hints: str,
        *,
        generation: int,
        impact_score: float = 0.5,
    ) -> None:
        self.add_many(split_hint_text(hints), generation=generation, impact_score=impact_score)

    def update_impact(self, text: str, new_score: float) -> None:
        """Update a hint's impact score."""
        normalized = _normalize_hint_text(text)
        for collection in (self._active, self._archived):
            hint = self._find_hint(normalized, collection)
            if hint is not None:
                hint.impact_score = new_score  # type: ignore[misc]
                self._reassign_ranks()
                return

    def active_hints(self) -> list[RankedHint]:
        """Return active hints sorted by impact (highest first)."""
        return sorted(
            self._active,
            key=lambda h: (-h.impact_score, -h.generation_added, h.text.lower()),
        )

    def archived_hints(self) -> list[RankedHint]:
        return sorted(
            self._archived,
            key=lambda h: (-h.impact_score, -h.generation_added, h.text.lower()),
        )

    def format_for_competitor(self) -> str:
        """Format active hints as competitor prompt context, ranked by impact."""
        ranked = self.active_hints()
        if not ranked:
            return ""
        lines = []
        for hint in ranked:
            lines.append(f"- {hint.text}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": {
                "max_hints": self._policy.max_hints,
                "archive_rotated": self._policy.archive_rotated,
            },
            "active": [hint.to_dict() for hint in self.active_hints()],
            "archived": [hint.to_dict() for hint in self.archived_hints()],
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        policy_override: HintVolumePolicy | None = None,
    ) -> HintManager:
        raw_policy = data.get("policy", {})
        policy = policy_override or HintVolumePolicy(
            max_hints=int(raw_policy.get("max_hints", 7)),
            archive_rotated=bool(raw_policy.get("archive_rotated", True)),
        )
        active = [
            RankedHint.from_dict(item)
            for item in data.get("active", [])
            if isinstance(item, dict)
        ]
        archived = [
            RankedHint.from_dict(item)
            for item in data.get("archived", [])
            if isinstance(item, dict)
        ]
        return cls(policy, active=active, archived=archived)

    @classmethod
    def from_hint_text(
        cls,
        hints: str,
        *,
        policy: HintVolumePolicy,
        generation: int = 0,
        impact_score: float = 0.5,
    ) -> HintManager:
        manager = cls(policy)
        manager.merge_hint_text(hints, generation=generation, impact_score=impact_score)
        return manager

    @staticmethod
    def _find_hint(text: str, collection: list[RankedHint]) -> RankedHint | None:
        normalized = _normalize_hint_text(text).lower()
        for hint in collection:
            if hint.text.lower() == normalized:
                return hint
        return None

    def _reassign_ranks(self) -> None:
        for idx, hint in enumerate(self.active_hints(), 1):
            hint.rank = idx

    def _enforce_cap(self) -> None:
        """Rotate out lowest-impact hints when over capacity."""
        while len(self._active) > self._policy.max_hints:
            # Sort by impact ascending — remove the lowest
            self._active.sort(key=lambda h: (h.impact_score, h.generation_added, h.text.lower()))
            removed = self._active.pop(0)
            if self._policy.archive_rotated:
                self._archived.append(removed)
        self._reassign_ranks()


def apply_volume_cap(
    hints: list[str],
    max_hints: int = 7,
) -> tuple[list[str], list[str]]:
    """Simple cap for hint string lists. Returns (active, archived)."""
    if len(hints) <= max_hints:
        return list(hints), []
    return hints[:max_hints], hints[max_hints:]
