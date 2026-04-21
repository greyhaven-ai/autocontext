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
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

_HINT_REFLECTION_MAX_HINTS = 4
_HINT_REFLECTION_MAX_HINT_CHARS = 72
_HINT_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.*\S)\s*$")
_HINT_MARKUP_RE = re.compile(r"[*`]+")
_HINT_WS_RE = re.compile(r"\s+")


def _sanitize_hint_text(text: str) -> str:
    cleaned = _HINT_MARKUP_RE.sub("", text)
    cleaned = _HINT_WS_RE.sub(" ", cleaned).strip()
    return cleaned


def _truncate_hint_text(text: str, *, limit: int = _HINT_REFLECTION_MAX_HINT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def prepare_hint_reflection_items(hints: str) -> list[str]:
    raw = hints.strip()
    if not raw:
        return []

    parsed_items: list[str] = []
    current_parts: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _HINT_LIST_ITEM_RE.match(line)
        if match:
            if current_parts:
                parsed_items.append(" ".join(current_parts))
            current_parts = [match.group(1).strip()]
            continue
        if current_parts:
            current_parts.append(stripped)
        else:
            current_parts = [stripped]
    if current_parts:
        parsed_items.append(" ".join(current_parts))

    normalized: list[str] = []
    seen: set[str] = set()
    for item in parsed_items:
        cleaned = _truncate_hint_text(_sanitize_hint_text(item))
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)
        if len(normalized) >= _HINT_REFLECTION_MAX_HINTS:
            break

    if normalized:
        return normalized
    fallback = _truncate_hint_text(_sanitize_hint_text(raw))
    return [fallback] if fallback else []


class HintFeedback(BaseModel):
    """Competitor's annotation of hint quality after tournament."""

    helpful: list[str] = Field(default_factory=list)
    misleading: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    generation: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("helpful", "misleading", "missing", mode="before")
    @classmethod
    def _normalize_feedback_items(cls, value: Any) -> list[str]:
        return _normalize_feedback_list(value)

    def is_empty(self) -> bool:
        return not self.helpful and not self.misleading and not self.missing

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HintFeedback:
        return cls.model_validate(data)


def build_hint_reflection_prompt(
    *,
    hints: str,
    tournament_best_score: float,
    tournament_mean_score: float,
    previous_best: float,
    hint_items: list[str] | None = None,
) -> str:
    """Build the post-tournament reflection prompt for the competitor."""
    compact_items = hint_items if hint_items is not None else prepare_hint_reflection_items(hints)
    hint_block = (
        "\n".join(f"{idx}. {item}" for idx, item in enumerate(compact_items, start=1))
        if compact_items
        else "(No hints were provided)"
    )

    return (
        "You just completed a tournament.\n\n"
        f"Coach hints used:\n{hint_block}\n\n"
        "Results: "
        f"best={tournament_best_score:.4f} "
        f"mean={tournament_mean_score:.4f} "
        f"previous_best={previous_best:.4f} "
        f"delta={tournament_best_score - previous_best:+.4f}.\n\n"
        'Return ONLY compact JSON: {"helpful_hint_numbers":[],"misleading_hint_numbers":[],"missing":[]}. '
        "Use only the hint numbers shown above. Keep missing items short."
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


def _normalize_feedback_index_list(value: Any, *, max_index: int) -> list[int]:
    if isinstance(value, (int, str)):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        return []

    normalized: list[int] = []
    seen: set[int] = set()
    for candidate in candidates:
        index: int | None = None
        if isinstance(candidate, int):
            index = candidate
        elif isinstance(candidate, str):
            stripped = candidate.strip()
            if stripped.isdigit():
                index = int(stripped)
        if index is None or index < 1 or index > max_index or index in seen:
            continue
        seen.add(index)
        normalized.append(index)
    return normalized


def parse_hint_feedback(
    raw_text: str,
    generation: int,
    *,
    hint_items: list[str] | None = None,
) -> HintFeedback:
    """Parse competitor's hint feedback response."""
    text = raw_text.strip()

    # Try fenced JSON first
    match = _JSON_FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            helpful: list[str]
            misleading: list[str]
            if hint_items:
                helpful_indexes = _normalize_feedback_index_list(
                    data.get("helpful_hint_numbers"),
                    max_index=len(hint_items),
                )
                misleading_indexes = _normalize_feedback_index_list(
                    data.get("misleading_hint_numbers"),
                    max_index=len(hint_items),
                )
                helpful = [hint_items[index - 1] for index in helpful_indexes]
                misleading = [hint_items[index - 1] for index in misleading_indexes]
            else:
                helpful = []
                misleading = []

            if not helpful:
                helpful = _normalize_feedback_list(data.get("helpful"))
            if not misleading:
                misleading = _normalize_feedback_list(data.get("misleading"))

            return HintFeedback(
                helpful=helpful,
                misleading=misleading,
                missing=_normalize_feedback_list(data.get("missing")),
                generation=generation,
            )
    except (json.JSONDecodeError, TypeError):
        logger.debug("agents.hint_feedback: suppressed json.JSONDecodeError), TypeError", exc_info=True)

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
