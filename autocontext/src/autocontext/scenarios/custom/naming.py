"""Deterministic scenario name derivation with domain-noun weighting (AC-285).

Extracts a human-readable, deterministic slug from natural-language
descriptions. Prefers domain nouns (drug, trial, proof, wargame) over
abstract adjectives (appropriateness, randomization) while preserving
collision handling and backward compatibility.

Functions:
- derive_name(): improved algorithm with noun weighting
- derive_name_legacy(): old algorithm (length-only) for backward compat
- resolve_alias(): look up old→new name mapping
- build_alias_map(): generate alias map between two naming functions
"""

from __future__ import annotations

import re
from collections.abc import Callable

# Shared stop words — kept in sync with creator.py and agent_task_creator.py
STOP_WORDS = frozenset({
    "a", "an", "the", "task", "where", "you", "with", "and", "or", "of", "for",
    "i", "want", "need", "make", "create", "build", "write", "develop", "implement",
    "that", "can", "should", "could", "would", "will", "must",
    "agent", "tool", "system",
    "clear", "well", "good", "great", "very", "really", "also", "just", "structured",
    "it", "we", "they", "is", "are", "was", "be", "do", "does",
    "to", "in", "on", "at", "by", "which", "what", "how",
    "game",
})

# Suffixes that signal abstract/adjective words (penalized in scoring)
_ABSTRACT_SUFFIXES = (
    "ness", "tion", "sion", "ment", "ity", "ous", "ive", "able",
    "ible", "ful", "less", "ence", "ance", "ical", "ally",
)


def _word_score(word: str, position: int, total_words: int) -> float:
    """Score a word for naming fitness.

    Higher = more likely to be a useful domain noun.
    Factors: not-abstract bonus, length bonus, position bonus (earlier better).
    """
    score = 0.0

    # Penalize abstract suffixes
    is_abstract = any(word.endswith(suffix) for suffix in _ABSTRACT_SUFFIXES)
    if is_abstract:
        score -= 2.0

    # Bonus for reasonable length (4-12 chars are sweet spot for domain nouns)
    if 4 <= len(word) <= 12:
        score += 2.0
    elif len(word) > 2:
        score += 1.0

    # Position bonus: earlier words in the description are more likely core domain terms
    if total_words > 0:
        position_weight = 1.0 - (position / total_words) * 0.5
        score += position_weight

    return score


def derive_name(
    description: str,
    stop_words: frozenset[str] | None = None,
) -> str:
    """Derive a deterministic, domain-descriptive name from a description.

    Improved algorithm that weights domain nouns above abstract adjectives
    and considers word position in the description.
    """
    if not description or not description.strip():
        return "custom"

    sw = stop_words if stop_words is not None else STOP_WORDS
    words = re.sub(r"[^a-z0-9\s]", " ", description.lower()).split()
    meaningful = [w for w in words if w not in sw and len(w) > 1]

    if not meaningful:
        return "custom"

    # Score each word and sort by score descending, breaking ties by position
    scored = [
        (_word_score(w, i, len(meaningful)), i, w)
        for i, w in enumerate(meaningful)
    ]
    scored.sort(key=lambda x: (-x[0], x[1]))

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for _, _, w in scored:
        if w not in seen:
            seen.add(w)
            unique.append(w)

    name_words = unique[:3] if len(unique) >= 3 else unique[:2] if unique else ["custom"]
    return "_".join(name_words)


def derive_name_legacy(
    description: str,
    stop_words: frozenset[str] | None = None,
) -> str:
    """Legacy name derivation — sorts by length descending (backward compat)."""
    if not description or not description.strip():
        return "custom"

    sw = stop_words if stop_words is not None else STOP_WORDS
    words = re.sub(r"[^a-z0-9\s]", " ", description.lower()).split()
    meaningful = [w for w in words if w not in sw]
    sorted_words = sorted(meaningful, key=len, reverse=True)

    seen: set[str] = set()
    unique: list[str] = []
    for w in sorted_words:
        if w not in seen:
            seen.add(w)
            unique.append(w)

    name_words = unique[:3] if len(unique) >= 3 else unique[:2] if unique else ["custom"]
    return "_".join(name_words)


def resolve_alias(
    name: str,
    aliases: dict[str, str],
) -> str:
    """Look up a name in an alias map, returning the new name or the original."""
    return aliases.get(name, name)


def build_alias_map(
    descriptions: list[str],
    old_fn: Callable[[str], str],
    new_fn: Callable[[str], str],
) -> dict[str, str]:
    """Build an alias map between old and new naming functions.

    Returns {old_name: new_name} for descriptions where the names differ.
    """
    aliases: dict[str, str] = {}
    for desc in descriptions:
        old = old_fn(desc)
        new = new_fn(desc)
        if old != new:
            aliases[old] = new
    return aliases
