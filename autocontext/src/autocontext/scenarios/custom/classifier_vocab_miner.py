"""AC-582 — mine cached LLM classifier fallback entries for vocabulary proposals.

The AC-580 fallback hands niche prompts to an LLM; AC-581 caches those results.
This module closes the feedback loop: it reads the cache, groups entries by
the family the LLM chose, and proposes new keyword signals that would let the
deterministic keyword classifier handle similar prompts without an LLM call.

Output is a markdown report for human review — never an automatic code change.

Layering:
    - :func:`mine_vocab_proposals` is a pure function over primitive data
      (dict entries, signal group dicts). Callable from tests without I/O.
    - :func:`format_proposals_report` renders proposals to markdown.
    - :func:`load_cache_entries` adapts a :class:`ClassifierCache` file into
      the entry shape the miner expects.

The miner DRYs on the classifier's stopword list — mining stopwords like
"and" or "the" is meaningless noise.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autocontext.scenarios.custom.family_classifier import _STOP_WORDS

_DEFAULT_SUGGESTED_WEIGHT = 1.5
_EXAMPLES_PER_PROPOSAL = 3
# Terms shorter than this are too noisy (e.g. "ok", "us").
_MIN_TERM_LENGTH = 4


@dataclass(slots=True, frozen=True)
class VocabProposal:
    """A proposed keyword signal addition for human review."""

    family_name: str
    term: str
    suggested_weight: float
    occurrence_count: int
    example_descriptions: tuple[str, ...] = field(default_factory=tuple)


def _tokenize(description: str) -> list[str]:
    """Lowercased word tokens, stopwords removed."""
    words = re.sub(r"[^a-z0-9\s\-]", " ", description.lower()).split()
    return [
        w for w in words
        if w not in _STOP_WORDS and len(w) >= _MIN_TERM_LENGTH
    ]


def _existing_signal_covers(
    candidate: str,
    signal_groups: dict[str, dict[str, float]],
) -> bool:
    """True when the candidate is already a substring match of any registered signal.

    Matches the classifier's own matching semantics: signals like ``escalat``
    are prefix substrings that fire on ``escalate`` and ``escalation``. So if
    a cached description's token is ``escalation``, the signal ``escalat``
    already covers it — don't propose ``escalation`` as a new keyword.
    """
    for signals in signal_groups.values():
        for signal in signals:
            if signal in candidate or candidate in signal:
                return True
    return False


def mine_vocab_proposals(
    cache_entries: list[dict[str, Any]],
    signal_groups: dict[str, dict[str, float]],
    *,
    min_occurrences: int = 3,
) -> list[VocabProposal]:
    """Return vocabulary-addition proposals from cached classifications.

    Algorithm:
        1. Group descriptions by ``family_name``.
        2. Tokenize each description (lowercase, stopwords removed, min length).
        3. Count tokens per family.
        4. Filter: count must meet ``min_occurrences``.
        5. Filter: token must not already be covered by any signal (substring).
        6. Sort by count descending; emit one :class:`VocabProposal` per term.
    """
    by_family: dict[str, list[str]] = {}
    for entry in cache_entries:
        family = entry.get("family_name")
        description = entry.get("description", "")
        if not family or not description:
            continue
        by_family.setdefault(family, []).append(description)

    proposals: list[VocabProposal] = []
    for family_name, descriptions in by_family.items():
        token_counts: Counter[str] = Counter()
        examples_by_term: dict[str, list[str]] = {}
        for description in descriptions:
            seen_in_this_desc: set[str] = set()
            for token in _tokenize(description):
                token_counts[token] += 1
                if token not in seen_in_this_desc:
                    examples_by_term.setdefault(token, []).append(description)
                    seen_in_this_desc.add(token)

        for term, count in token_counts.most_common():
            if count < min_occurrences:
                break  # most_common is sorted; everything else is below
            if _existing_signal_covers(term, signal_groups):
                continue
            examples = tuple(examples_by_term[term][:_EXAMPLES_PER_PROPOSAL])
            proposals.append(
                VocabProposal(
                    family_name=family_name,
                    term=term,
                    suggested_weight=_DEFAULT_SUGGESTED_WEIGHT,
                    occurrence_count=count,
                    example_descriptions=examples,
                )
            )
    return proposals


def format_proposals_report(
    proposals: list[VocabProposal],
    *,
    total_cache_entries: int,
) -> str:
    """Render proposals as a human-readable markdown report."""
    lines: list[str] = []
    lines.append("# Classifier Vocabulary Mining Report")
    lines.append("")
    lines.append(f"- Cache entries analyzed: **{total_cache_entries}**")
    lines.append(f"- Proposals generated: **{len(proposals)}**")
    lines.append("")

    if not proposals:
        lines.append("No vocabulary proposals — the keyword classifier already covers every cached family.")
        return "\n".join(lines) + "\n"

    by_family: dict[str, list[VocabProposal]] = {}
    for p in proposals:
        by_family.setdefault(p.family_name, []).append(p)

    for family_name in sorted(by_family):
        lines.append(f"## Family: `{family_name}`")
        lines.append("")
        lines.append("| Term | Suggested weight | Occurrences | Example |")
        lines.append("| --- | ---: | ---: | --- |")
        for proposal in by_family[family_name]:
            example = proposal.example_descriptions[0] if proposal.example_descriptions else ""
            example = example.replace("|", "\\|").replace("\n", " ")
            if len(example) > 80:
                example = example[:77] + "..."
            lines.append(
                f"| `{proposal.term}` "
                f"| {proposal.suggested_weight:.1f} "
                f"| {proposal.occurrence_count} "
                f"| {example} |"
            )
        lines.append("")
        # Include extra examples below the table for proposals with multiple.
        for proposal in by_family[family_name]:
            if len(proposal.example_descriptions) > 1:
                lines.append(f"**`{proposal.term}` additional examples:**")
                for ex in proposal.example_descriptions[1:]:
                    lines.append(f"- {ex}")
                lines.append("")

    return "\n".join(lines) + "\n"


def load_cache_entries(cache_path: Path) -> list[dict[str, Any]]:
    """Read entries from a :class:`ClassifierCache` file for mining.

    Returns a list of ``{"description": str, "family_name": str}`` dicts.
    Silently skips entries missing either field — tolerates the legacy cache
    format from the first AC-581 ship that stored only SHA-256 keys.
    """
    import json

    try:
        raw = cache_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return []

    normalized: list[dict[str, Any]] = []
    for value in entries.values():
        if not isinstance(value, dict):
            continue
        description = value.get("description")
        family_name = value.get("family_name")
        if not isinstance(description, str) or not isinstance(family_name, str):
            continue
        normalized.append({"description": description, "family_name": family_name})
    return normalized


def _default_cache_path() -> Path:
    """Where the AC-581 cache is expected by default."""
    import os

    override = os.getenv("AUTOCONTEXT_CLASSIFIER_CACHE_DIR")
    base = Path(override) if override else Path.home() / ".cache" / "autocontext"
    return base / "classifier_fallback.json"
