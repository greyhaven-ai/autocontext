"""Feedback loops for analyst quality scoring and tool usage tracking (AC-335 + AC-336).

Closes two broken incentive loops:
- AC-336: Analyst gets rated by curator on actionability/specificity/correctness
- AC-335: Architect gets tool usage data showing which tools the competitor uses

Key types:
- AnalystRating: 1-5 scores for analyst output quality
- format_analyst_feedback(): formats rating for next analyst prompt
- ToolUsageRecord: per-tool usage stats
- ToolUsageTracker: scans strategy text for tool references
- format_utilization_report(): formats usage for architect prompt
- identify_stale_tools(): finds tools unused for N generations
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# AC-336: Analyst quality scoring
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AnalystRating:
    """Curator's quality rating for analyst output."""

    actionability: int  # 1-5
    specificity: int  # 1-5
    correctness: int  # 1-5
    rationale: str
    generation: int
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def overall(self) -> float:
        return round(statistics.mean([self.actionability, self.specificity, self.correctness]), 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "actionability": self.actionability,
            "specificity": self.specificity,
            "correctness": self.correctness,
            "rationale": self.rationale,
            "generation": self.generation,
            "overall": self.overall,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalystRating:
        return cls(
            actionability=data.get("actionability", 3),
            specificity=data.get("specificity", 3),
            correctness=data.get("correctness", 3),
            rationale=data.get("rationale", ""),
            generation=data.get("generation", 0),
            metadata=data.get("metadata", {}),
        )


def format_analyst_feedback(rating: AnalystRating | None) -> str:
    """Format analyst rating as feedback for the next generation's analyst prompt."""
    if rating is None:
        return ""

    return (
        f"## Previous Analysis Quality (Gen {rating.generation})\n"
        f"Curator rating: {rating.overall:.1f}/5.0\n"
        f"- Actionability: {rating.actionability}/5\n"
        f"- Specificity: {rating.specificity}/5\n"
        f"- Correctness: {rating.correctness}/5\n"
        f"\nCurator feedback: {rating.rationale}\n"
    )


# ---------------------------------------------------------------------------
# AC-335: Tool usage tracking
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ToolUsageRecord:
    """Per-tool usage statistics."""

    tool_name: str
    used_in_gens: list[int]
    last_used: int
    total_refs: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "used_in_gens": self.used_in_gens,
            "last_used": self.last_used,
            "total_refs": self.total_refs,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolUsageRecord:
        return cls(
            tool_name=data["tool_name"],
            used_in_gens=data.get("used_in_gens", []),
            last_used=data.get("last_used", 0),
            total_refs=data.get("total_refs", 0),
            metadata=data.get("metadata", {}),
        )


class ToolUsageTracker:
    """Tracks tool name references in competitor strategy text."""

    def __init__(self, known_tools: list[str]) -> None:
        self._tools = known_tools
        self._records: dict[str, ToolUsageRecord] = {
            name: ToolUsageRecord(tool_name=name, used_in_gens=[], last_used=0, total_refs=0)
            for name in known_tools
        }

    def record_generation(self, generation: int, strategy_text: str) -> None:
        """Scan strategy text for tool references and update stats."""
        text_lower = strategy_text.lower()
        for name in self._tools:
            if name.lower() in text_lower:
                rec = self._records[name]
                if generation not in rec.used_in_gens:
                    rec.used_in_gens.append(generation)
                rec.last_used = max(rec.last_used, generation)
                rec.total_refs += 1

    def get_stats(self) -> dict[str, ToolUsageRecord]:
        return dict(self._records)


def format_utilization_report(tracker: ToolUsageTracker, window: int = 5) -> str:
    """Format tool usage stats as a utilization report for the architect prompt."""
    stats = tracker.get_stats()
    if not stats:
        return ""

    lines = [f"Tool utilization (last {window} gens):"]
    for name, rec in sorted(stats.items()):
        recent_uses = sum(1 for g in rec.used_in_gens if g > (rec.last_used - window))
        if rec.total_refs == 0:
            level = "UNUSED"
        elif recent_uses >= window * 0.6:
            level = "HIGH"
        elif recent_uses >= 1:
            level = "LOW"
        else:
            level = "UNUSED"
        lines.append(f"- {name}: used {recent_uses}/{window} gens ({level})")

    return "\n".join(lines)


def identify_stale_tools(
    tracker: ToolUsageTracker,
    current_generation: int,
    archive_after_gens: int = 5,
) -> list[str]:
    """Find tools unused for archive_after_gens generations."""
    stale: list[str] = []
    for name, rec in tracker.get_stats().items():
        if rec.last_used == 0:
            # Never used — stale if enough generations have passed
            if current_generation >= archive_after_gens:
                stale.append(name)
        elif current_generation - rec.last_used >= archive_after_gens:
            stale.append(name)
    return stale
