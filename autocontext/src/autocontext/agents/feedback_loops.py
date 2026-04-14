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

import json
import statistics
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator

# ---------------------------------------------------------------------------
# AC-336: Analyst quality scoring
# ---------------------------------------------------------------------------


class AnalystRating(BaseModel):
    """Curator's quality rating for analyst output."""

    actionability: int = 3  # 1-5
    specificity: int = 3  # 1-5
    correctness: int = 3  # 1-5
    rationale: str = ""
    generation: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @computed_field(return_type=float)
    def overall(self) -> float:
        return round(statistics.mean([self.actionability, self.specificity, self.correctness]), 2)

    @field_validator("rationale", mode="before")
    @classmethod
    def _coerce_rationale(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict | list):
            return json.dumps(value, sort_keys=True)
        return str(value)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalystRating:
        return cls.model_validate(data)


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


class ToolUsageRecord(BaseModel):
    """Per-tool usage statistics."""

    tool_name: str
    used_in_gens: list[int]
    last_used: int
    total_refs: int
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolUsageRecord:
        return cls.model_validate(data)


class ToolUsageTracker:
    """Tracks tool name references in competitor strategy text."""

    def __init__(self, known_tools: list[str]) -> None:
        self._tools = known_tools
        self._records: dict[str, ToolUsageRecord] = {
            name: ToolUsageRecord(tool_name=name, used_in_gens=[], last_used=0, total_refs=0) for name in known_tools
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": {name: record.to_dict() for name, record in sorted(self._records.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], known_tools: list[str]) -> ToolUsageTracker:
        tracker = cls(known_tools=known_tools)
        raw_records = data.get("records", {})
        if not isinstance(raw_records, dict):
            return tracker
        for name, raw in raw_records.items():
            if not isinstance(name, str) or not isinstance(raw, dict):
                continue
            tracker._records[name] = ToolUsageRecord.from_dict(raw)
        for name in known_tools:
            tracker._records.setdefault(
                name,
                ToolUsageRecord(tool_name=name, used_in_gens=[], last_used=0, total_refs=0),
            )
        return tracker


def format_utilization_report(
    tracker: ToolUsageTracker,
    current_generation: int,
    window: int = 5,
) -> str:
    """Format tool usage stats as a utilization report for the architect prompt."""
    stats = tracker.get_stats()
    if not stats:
        return ""

    lines = [f"Tool utilization (last {window} gens):"]
    for name, rec in sorted(stats.items()):
        recent_uses = sum(1 for g in rec.used_in_gens if 0 <= current_generation - g < window)
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
