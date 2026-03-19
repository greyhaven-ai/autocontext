"""Bidirectional competitor hint feedback after tournament (AC-337).

After tournament, the competitor annotates which hints were helpful,
misleading, or missing based on actual match outcomes. This signal
flows back to the coach for faster hint correction.

Key types:
- HintFeedback: structured helpful/misleading/missing annotations
- build_hint_reflection_prompt(): prompt for competitor reflection
- parse_hint_feedback(): parse competitor's JSON response
- format_hint_feedback_for_coach(): format for coach injection
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class HintFeedback:
    """Competitor's annotation of hint quality after tournament."""

    helpful: list[str]
    misleading: list[str]
    missing: list[str]
    generation: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.helpful and not self.misleading and not self.missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "helpful": self.helpful,
            "misleading": self.misleading,
            "missing": self.missing,
            "generation": self.generation,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HintFeedback:
        return cls(
            helpful=_normalize_feedback_list(data.get("helpful")),
            misleading=_normalize_feedback_list(data.get("misleading")),
            missing=_normalize_feedback_list(data.get("missing")),
            generation=data.get("generation", 0),
            metadata=data.get("metadata", {}),
        )


def build_hint_reflection_prompt(
    *,
    hints: str,
    tournament_best_score: float,
    tournament_mean_score: float,
    previous_best: float,
) -> str:
    """Build the post-tournament reflection prompt for the competitor."""
    hint_block = hints.strip() if hints.strip() else "(No hints were provided)"

    return (
        "You just completed a tournament. Reflect on the coach's hints "
        "and annotate which were helpful, misleading, or missing.\n\n"
        f"## Coach Hints Used\n{hint_block}\n\n"
        f"## Tournament Results\n"
        f"Best score: {tournament_best_score:.4f}\n"
        f"Mean score: {tournament_mean_score:.4f}\n"
        f"Previous best: {previous_best:.4f}\n"
        f"Delta: {tournament_best_score - previous_best:+.4f}\n\n"
        "## Your Task\n"
        "Based on your actual match experience, annotate the hints:\n"
        "Return a JSON object with three lists:\n"
        '- "helpful": hints that led to good outcomes\n'
        '- "misleading": hints that were wrong or counterproductive\n'
        '- "missing": guidance you needed but didn\'t receive\n\n'
        "Return ONLY the JSON object, no commentary."
    )


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def _normalize_feedback_list(value: Any) -> list[str]:
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                normalized.append(cleaned)
    return normalized


def parse_hint_feedback(raw_text: str, generation: int) -> HintFeedback:
    """Parse competitor's hint feedback response."""
    text = raw_text.strip()

    # Try fenced JSON first
    match = _JSON_FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return HintFeedback(
                helpful=_normalize_feedback_list(data.get("helpful")),
                misleading=_normalize_feedback_list(data.get("misleading")),
                missing=_normalize_feedback_list(data.get("missing")),
                generation=generation,
            )
    except (json.JSONDecodeError, TypeError):
        pass

    return HintFeedback(helpful=[], misleading=[], missing=[], generation=generation)


def format_hint_feedback_for_coach(feedback: HintFeedback | None) -> str:
    """Format hint feedback as context for the coach's next prompt."""
    if feedback is None or feedback.is_empty():
        return ""

    sections: list[str] = [
        f"## Competitor Hint Feedback (Gen {feedback.generation})",
    ]

    if feedback.helpful:
        items = "\n".join(f"- {h}" for h in feedback.helpful)
        sections.append(f"\n### Helpful Hints\n{items}")

    if feedback.misleading:
        items = "\n".join(f"- {m}" for m in feedback.misleading)
        sections.append(f"\n### Misleading Hints (correct or remove)\n{items}")

    if feedback.missing:
        items = "\n".join(f"- {m}" for m in feedback.missing)
        sections.append(f"\n### Missing Guidance (add next time)\n{items}")

    return "\n".join(sections)
