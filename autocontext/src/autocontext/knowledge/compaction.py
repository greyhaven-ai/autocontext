"""Deterministic context compaction for long-lived knowledge surfaces.

These helpers keep structured context bounded before the final prompt budget
fallback runs. The goal is not to perfectly summarize arbitrary text, but to
preserve high-signal structure such as headings, bullets, findings, and recent
history while dropping repetitive filler.
"""

from __future__ import annotations

import re
import secrets
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from autocontext.prompts.context_budget import estimate_tokens

_DEFAULT_COMPONENT_TOKEN_LIMITS: dict[str, int] = {
    "playbook": 2800,
    "lessons": 1600,
    "analysis": 1800,
    "trajectory": 1200,
    "experiment_log": 1800,
    "session_reports": 1400,
    "research_protocol": 1200,
    "evidence_manifest": 1200,
    "evidence_manifest_analyst": 1200,
    "evidence_manifest_architect": 1200,
    "agent_task_playbook": 600,
    "agent_task_best_output": 900,
    "policy_refinement_rules": 1600,
    "policy_refinement_interface": 1000,
    "policy_refinement_criteria": 1000,
    "policy_refinement_feedback": 1400,
    "consultation_context": 400,
    "consultation_strategy": 400,
}

_TAIL_PRESERVING_COMPONENTS = {
    "agent_task_best_output",
    "consultation_context",
    "consultation_strategy",
}

_COMPACTION_POLICY_VERSION = "semantic-compaction-v1"
_COMPACTION_CACHE_MAX_SIZE = 512
_COMPACTION_CACHE: OrderedDict[tuple[str, str, str, int], tuple[str, str]] = OrderedDict()
_COMPACTION_CACHE_HITS = 0
_COMPACTION_CACHE_MISSES = 0

_IMPORTANT_KEYWORDS = (
    "root cause",
    "finding",
    "findings",
    "recommendation",
    "recommendations",
    "rollback",
    "guard",
    "freshness",
    "objective",
    "score",
    "hypothesis",
    "diagnosis",
    "regression",
    "failure",
    "mitigation",
)


@dataclass(frozen=True, slots=True)
class CompactionEntry:
    """Pi-shaped ledger entry describing one semantic compaction boundary."""

    entry_id: str
    parent_id: str
    timestamp: str
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "compaction",
            "id": self.entry_id,
            "parentId": self.parent_id,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "firstKeptEntryId": self.first_kept_entry_id,
            "tokensBefore": self.tokens_before,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CompactionEntry:
        details = data.get("details")
        return cls(
            entry_id=str(data.get("id", "")),
            parent_id=str(data.get("parentId", "")),
            timestamp=str(data.get("timestamp", "")),
            summary=str(data.get("summary", "")),
            first_kept_entry_id=str(data.get("firstKeptEntryId", "")),
            tokens_before=_coerce_int(data.get("tokensBefore")),
            details=dict(details) if isinstance(details, Mapping) else {},
        )


@dataclass(frozen=True, slots=True)
class PromptCompactionResult:
    components: dict[str, str]
    entries: list[CompactionEntry]


def compact_prompt_components(components: Mapping[str, str]) -> dict[str, str]:
    """Return a compacted copy of prompt-facing context components."""
    return _compact_prompt_components(components)


def compact_prompt_components_with_entries(
    components: Mapping[str, str],
    *,
    context: Mapping[str, Any] | None = None,
    parent_id: str = "",
    id_factory: Callable[[], str] | None = None,
    timestamp_factory: Callable[[], str] | None = None,
) -> PromptCompactionResult:
    """Return compacted components plus Pi-shaped ledger entries for changed components."""
    result = _compact_prompt_components(components)
    entries = compaction_entries_for_components(
        components,
        result,
        context=context,
        parent_id=parent_id,
        id_factory=id_factory,
        timestamp_factory=timestamp_factory,
    )
    return PromptCompactionResult(components=result, entries=entries)


def _compact_prompt_components(components: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in components.items():
        result[key] = compact_prompt_component(key, value)
    return result


def compaction_entries_for_components(
    original_components: Mapping[str, str],
    compacted_components: Mapping[str, str],
    *,
    context: Mapping[str, Any] | None = None,
    parent_id: str = "",
    id_factory: Callable[[], str] | None = None,
    timestamp_factory: Callable[[], str] | None = None,
) -> list[CompactionEntry]:
    """Build ledger entries by comparing original and final compacted components."""
    entries: list[CompactionEntry] = []
    current_parent_id = parent_id
    next_id = id_factory or _new_entry_id
    next_timestamp = timestamp_factory or _utc_timestamp
    for key, value in original_components.items():
        compacted = compacted_components.get(key, value)
        if not value or compacted == value:
            continue
        entry_id = next_id()
        entry = _build_compaction_entry(
            key=key,
            original=value,
            compacted=compacted,
            entry_id=entry_id,
            parent_id=current_parent_id,
            timestamp=next_timestamp(),
            context=context or {},
        )
        entries.append(entry)
        current_parent_id = entry_id
    return entries


def _build_compaction_entry(
    *,
    key: str,
    original: str,
    compacted: str,
    entry_id: str,
    parent_id: str,
    timestamp: str,
    context: Mapping[str, Any],
) -> CompactionEntry:
    tokens_before = estimate_tokens(original)
    tokens_after = estimate_tokens(compacted)
    details: dict[str, Any] = {
        "component": key,
        "source": "prompt_components",
        "tokensAfter": tokens_after,
        "contentLengthBefore": len(original),
        "contentLengthAfter": len(compacted),
    }
    details.update(dict(context))
    summary = _structured_compaction_summary(key, tokens_before, tokens_after, compacted)
    return CompactionEntry(
        entry_id=entry_id,
        parent_id=parent_id,
        timestamp=timestamp,
        summary=summary,
        first_kept_entry_id=f"component:{key}:kept",
        tokens_before=tokens_before,
        details=details,
    )


def _structured_compaction_summary(key: str, tokens_before: int, tokens_after: int, compacted: str) -> str:
    context = _truncate_text(compacted, max_tokens=650).strip()
    return (
        "## Goal\n"
        f"Keep prompt component `{key}` resumable after semantic compaction.\n\n"
        "## Progress\n"
        "### Done\n"
        f"- Compacted `{key}` from {tokens_before} to {tokens_after} estimated tokens.\n\n"
        "## Critical Context\n"
        f"{context}"
    ).strip()


def _new_entry_id() -> str:
    return secrets.token_hex(4)


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def compact_prompt_component(key: str, value: str) -> str:
    """Compact a single prompt-facing component when a limit is configured."""
    if not isinstance(value, str):
        value = str(value)
    if not value:
        return value
    limit = _DEFAULT_COMPONENT_TOKEN_LIMITS.get(key)
    if limit is None:
        return value
    return _cached_compact_component(key, value, limit)


def clear_prompt_compaction_cache() -> None:
    """Clear the in-process semantic compaction cache."""
    global _COMPACTION_CACHE_HITS, _COMPACTION_CACHE_MISSES
    _COMPACTION_CACHE.clear()
    _COMPACTION_CACHE_HITS = 0
    _COMPACTION_CACHE_MISSES = 0


def prompt_compaction_cache_stats() -> dict[str, int]:
    """Return lightweight cache counters for tests and diagnostics."""
    return {
        "entries": len(_COMPACTION_CACHE),
        "hits": _COMPACTION_CACHE_HITS,
        "misses": _COMPACTION_CACHE_MISSES,
    }


def extract_promotable_lines(text: str, *, max_items: int = 3) -> list[str]:
    """Extract durable lessons from a report-like block of markdown."""
    if not text.strip():
        return []

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates: list[str] = []
    seen: set[str] = set()

    prioritized_lines: list[str] = []
    fallback_lines: list[str] = []

    for line in lines:
        normalized = line.lower()
        cleaned = re.sub(r"\s+", " ", line).strip().lstrip("#").strip().lstrip("-* ").strip()
        if not cleaned or cleaned.lower() in seen:
            continue
        if line.startswith("#"):
            if cleaned.lower() not in {"findings", "summary"} and not cleaned.lower().startswith("session report"):
                fallback_lines.append(cleaned)
        elif line.startswith(("- ", "* ")) or any(keyword in normalized for keyword in _IMPORTANT_KEYWORDS):
            prioritized_lines.append(cleaned)

    for cleaned in [*prioritized_lines, *fallback_lines]:
        if cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        candidates.append(cleaned[:220])
        if len(candidates) >= max_items:
            break

    if candidates:
        return candidates

    fallback = re.sub(r"\s+", " ", text).strip()
    return [fallback[:220]] if fallback else []


def _compact_component(key: str, text: str, max_tokens: int) -> str:
    if key in {"experiment_log", "session_reports", "policy_refinement_feedback"}:
        needs_history_compaction = len(text.splitlines()) > 24 or len(_split_sections(text)) > 4
        if not needs_history_compaction and estimate_tokens(text) <= max_tokens:
            return text
    elif estimate_tokens(text) <= max_tokens:
        return text

    if key in {"experiment_log", "session_reports", "policy_refinement_feedback"}:
        compacted = _compact_history(text, max_tokens=max_tokens)
    elif key == "trajectory":
        compacted = _compact_table(text, max_tokens=max_tokens)
    elif key in _TAIL_PRESERVING_COMPONENTS and _looks_like_plain_prose(text):
        compacted = _compact_plain_prose(text, max_tokens=max_tokens)
    elif key == "lessons":
        compacted = _compact_markdown(text, max_tokens=max_tokens, prefer_recent=True)
    else:
        compacted = _compact_markdown(text, max_tokens=max_tokens)

    if estimate_tokens(compacted) > max_tokens:
        compacted = _truncate_text(compacted, max_tokens=max_tokens)
    return compacted


def _cached_compact_component(key: str, text: str, max_tokens: int) -> str:
    global _COMPACTION_CACHE_HITS, _COMPACTION_CACHE_MISSES
    cache_key = (
        _COMPACTION_POLICY_VERSION,
        key,
        sha256(text.encode("utf-8")).hexdigest(),
        max_tokens,
    )
    cached = _COMPACTION_CACHE.get(cache_key)
    if cached is not None and cached[0] == text:
        _COMPACTION_CACHE_HITS += 1
        _COMPACTION_CACHE.move_to_end(cache_key)
        return cached[1]

    _COMPACTION_CACHE_MISSES += 1
    compacted = _compact_component(key, text, max_tokens)
    _COMPACTION_CACHE[cache_key] = (text, compacted)
    _COMPACTION_CACHE.move_to_end(cache_key)
    while len(_COMPACTION_CACHE) > _COMPACTION_CACHE_MAX_SIZE:
        _COMPACTION_CACHE.popitem(last=False)
    return compacted


def _compact_history(text: str, *, max_tokens: int) -> str:
    sections = _split_sections(text)
    if not sections:
        return _truncate_text(text, max_tokens=max_tokens)

    selected = sections[-4:]
    compacted_sections = [_compact_section(section) for section in selected]
    compacted = "\n\n".join(section for section in compacted_sections if section.strip()).strip()
    if compacted and compacted != text:
        compacted = f"{compacted}\n\n[... condensed recent history ...]"
    return compacted or _truncate_text(text, max_tokens=max_tokens)


def _compact_markdown(
    text: str,
    *,
    max_tokens: int,
    prefer_recent: bool = False,
) -> str:
    sections = _split_sections(text)
    if not sections:
        return _truncate_text(text, max_tokens=max_tokens)

    selected_sections = sections[-6:] if prefer_recent else sections[:6]
    compacted_sections = [
        _compact_section(section, prefer_recent=prefer_recent)
        for section in selected_sections
    ]
    compacted = "\n\n".join(section for section in compacted_sections if section.strip()).strip()
    if compacted and compacted != text:
        compacted = f"{compacted}\n\n[... condensed structured context ...]"
    return compacted or _truncate_text(text, max_tokens=max_tokens)


def _compact_table(text: str, *, max_tokens: int) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    if len(lines) <= 12 and estimate_tokens(text) <= max_tokens:
        return text

    table_header: list[str] = []
    table_rows: list[str] = []
    pre_table_lines: list[str] = []
    post_table_lines: list[str] = []
    in_table = False
    saw_table = False

    for line in lines:
        if line.startswith("|"):
            in_table = True
            saw_table = True
            if len(table_header) < 2:
                table_header.append(line)
            else:
                table_rows.append(line)
        elif in_table and not line.strip():
            in_table = False
        else:
            target = post_table_lines if saw_table and not in_table else pre_table_lines
            target.append(line)

    selected_rows = table_rows[-8:]
    trailing_context = "\n".join(line for line in post_table_lines if line.strip()).strip()
    compacted_trailing_context = (
        _compact_markdown(trailing_context, max_tokens=max_tokens)
        if trailing_context
        else ""
    )
    compacted_lines = [
        *pre_table_lines[:4],
        *table_header,
        *selected_rows,
    ]
    if compacted_trailing_context:
        compacted_lines.extend(["", compacted_trailing_context])
    compacted = "\n".join(line for line in compacted_lines if line is not None).strip()
    if compacted and compacted != text:
        compacted = f"{compacted}\n\n[... condensed trajectory ...]"
    return compacted or _truncate_text(text, max_tokens=max_tokens)


def _compact_plain_prose(text: str, *, max_tokens: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return _truncate_text(text, max_tokens=max_tokens)

    head = lines[:2]
    tail = lines[-3:]
    selected = _dedupe_lines([*head, *tail])
    compacted = "\n".join(selected).strip()
    if compacted and compacted != text:
        compacted = f"{compacted}\n\n[... condensed recent context ...]"
    return compacted or _truncate_text(text, max_tokens=max_tokens)


def _split_sections(text: str) -> list[str]:
    if "\n\n---\n\n" in text:
        return [section.strip() for section in text.split("\n\n---\n\n") if section.strip()]

    sections: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s+", line) and current:
            sections.append(current)
            current = [line]
            continue
        current.append(line)
    if current:
        sections.append(current)
    return ["\n".join(section).strip() for section in sections if any(line.strip() for line in section)]


def _looks_like_plain_prose(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if re.search(r"^#{1,6}\s+", stripped, re.MULTILINE):
        return False
    if "\n\n---\n\n" in stripped:
        return False
    if re.search(r"^\s*(?:[-*]|\d+\.)\s+", stripped, re.MULTILINE):
        return False
    return True


def _compact_section(section: str, *, prefer_recent: bool = False) -> str:
    lines = [line.rstrip() for line in section.splitlines() if line.strip()]
    if not lines:
        return ""

    selected: list[str] = []
    heading_kept = False
    body_candidates: list[str] = []

    for line in lines:
        stripped = line.strip()
        normalized = stripped.lower()
        if stripped.startswith("#"):
            if not heading_kept:
                selected.append(stripped)
                heading_kept = True
            continue
        if _is_structured_line(stripped) or any(keyword in normalized for keyword in _IMPORTANT_KEYWORDS):
            body_candidates.append(stripped)

    if not body_candidates:
        body_candidates = [line.strip() for line in lines[1:3] if line.strip()] or [lines[0].strip()]

    deduped_candidates = _dedupe_lines(body_candidates)
    chosen_candidates = deduped_candidates[-4:] if prefer_recent else deduped_candidates[:4]
    selected.extend(chosen_candidates)
    return "\n".join(selected).strip()


def _is_structured_line(line: str) -> bool:
    return (
        line.startswith(("- ", "* ", "> "))
        or bool(re.match(r"^\d+\.\s+", line))
        or ":" in line
    )


def _dedupe_lines(lines: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = re.sub(r"\s+", " ", line.strip()).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(line.strip())
    return deduped


def _truncate_text(text: str, *, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rstrip()
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars // 2:
        truncated = truncated[:last_nl].rstrip()
    return f"{truncated}\n[... condensed for prompt budget ...]"
