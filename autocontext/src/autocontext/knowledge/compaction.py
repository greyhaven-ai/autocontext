"""Deterministic context compaction for long-lived knowledge surfaces.

These helpers keep structured context bounded before the final prompt budget
fallback runs. The goal is not to perfectly summarize arbitrary text, but to
preserve high-signal structure such as headings, bullets, findings, and recent
history while dropping repetitive filler.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

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
}

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


def compact_prompt_components(components: Mapping[str, str]) -> dict[str, str]:
    """Return a compacted copy of prompt-facing context components."""
    result: dict[str, str] = {}
    for key, value in components.items():
        if not value:
            result[key] = value
            continue
        limit = _DEFAULT_COMPONENT_TOKEN_LIMITS.get(key)
        if limit is None:
            result[key] = value
            continue
        result[key] = _compact_component(key, value, limit)
    return result


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
    if key in {"experiment_log", "session_reports"}:
        needs_history_compaction = len(text.splitlines()) > 24 or len(_split_sections(text)) > 4
        if not needs_history_compaction and estimate_tokens(text) <= max_tokens:
            return text
    elif estimate_tokens(text) <= max_tokens:
        return text

    if key in {"experiment_log", "session_reports"}:
        compacted = _compact_history(text, max_tokens=max_tokens)
    elif key == "trajectory":
        compacted = _compact_table(text, max_tokens=max_tokens)
    elif key == "lessons":
        compacted = _compact_markdown(text, max_tokens=max_tokens, prefer_recent=True)
    else:
        compacted = _compact_markdown(text, max_tokens=max_tokens)

    if estimate_tokens(compacted) > max_tokens:
        compacted = _truncate_text(compacted, max_tokens=max_tokens)
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
