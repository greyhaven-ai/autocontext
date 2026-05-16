"""AC-706: redaction policy for Hermes session and trajectory imports.

Sessions and trajectories carry raw model prompts and responses, which
can include secrets, tokens, credentials, PII, and user content the
operator did not intend to share. Every Hermes import path that touches
content goes through this module so the redaction posture is consistent
across `ingest-sessions` and `ingest-trajectories`.

This module is a thin policy layer over ``autocontext.sharing.redactor``
(AC-519). The session sharing path already handles the common high-risk
patterns (Anthropic / OpenAI / AWS / GitHub / Slack keys, bearer
tokens, emails, IPs, env values, absolute paths, ssh/kube/aws config
references). The Hermes wrapper adds:

* `RedactionPolicy` with named modes (`off` / `standard` / `strict`),
* user-defined regex patterns (per AC-706 acceptance criteria),
* a single ``redact_text`` entry point that returns the redacted string
  plus a per-call ``RedactionStats`` count breakdown so the ingester
  can include it in the JSONL output and the CLI summary.

Markers are intentionally preserved (e.g. ``[REDACTED_API_KEY]``) so
downstream training and evaluation can reason about what was removed
without needing the original content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from autocontext.sharing.redactor import redact_content_with_report

_USER_REPLACEMENT_TEMPLATE = "[REDACTED_USER_PATTERN:{name}]"


@dataclass(frozen=True, slots=True)
class UserPattern:
    """A caller-supplied regex pattern with a stable name.

    The name appears in the redaction marker (``[REDACTED_USER_PATTERN:<name>]``)
    so downstream consumers can tell different user patterns apart.
    """

    name: str
    pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """Configuration for a single ingest call.

    Modes:
    * ``off``: no redaction. Only valid when the operator explicitly opts
      in (the CLI default is ``standard``).
    * ``standard``: full ``sharing/redactor`` pipeline (keys, PII, env,
      absolute paths, high-risk file refs).
    * ``strict``: ``standard`` plus caller-provided user patterns.
    """

    mode: str = "standard"
    user_patterns: tuple[UserPattern, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"off", "standard", "strict"}:
            raise ValueError(f"unknown redaction mode {self.mode!r}; expected off|standard|strict")
        if self.mode == "strict" and not self.user_patterns:
            # Strict mode without user patterns is functionally identical to
            # standard. Surface that early so the operator notices.
            raise ValueError("redaction mode 'strict' requires at least one user pattern")


@dataclass(slots=True)
class RedactionStats:
    """Per-call summary of how many redactions were applied, by category."""

    total: int = 0
    by_category: dict[str, int] = field(default_factory=dict)

    def add(self, category: str, count: int = 1) -> None:
        self.total += count
        self.by_category[category] = self.by_category.get(category, 0) + count

    def to_dict(self) -> dict[str, Any]:
        return {"total": self.total, "by_category": dict(self.by_category)}


def redact_text(text: str, policy: RedactionPolicy) -> tuple[str, RedactionStats]:
    """Apply ``policy`` to ``text``; return ``(redacted, stats)``.

    ``stats`` carries the per-category counts so the ingester can write
    them into the output JSONL and the CLI summary. Order: built-in
    patterns first (via ``sharing/redactor``), then user patterns. This
    keeps the order stable across modes and ensures user patterns can
    target content that the built-in pipeline left alone.
    """

    stats = RedactionStats()
    if policy.mode == "off" or not text:
        return text, stats

    redacted, report = redact_content_with_report(text)
    for r in report.redactions:
        stats.add(r.category)

    if policy.mode == "strict":
        for user_pattern in policy.user_patterns:
            replacement = _USER_REPLACEMENT_TEMPLATE.format(name=user_pattern.name)
            new_text, hits = user_pattern.pattern.subn(replacement, redacted)
            if hits > 0:
                redacted = new_text
                stats.add(f"user_pattern:{user_pattern.name}", hits)

    return redacted, stats


def compile_user_patterns(raw: list[dict[str, str]] | None) -> tuple[UserPattern, ...]:
    """Compile caller-supplied ``[{"name": ..., "pattern": ...}]`` entries.

    Rejects entries with no name or an uncompilable pattern; raises
    ``ValueError`` with a clear message so the CLI can surface the bad
    input rather than silently dropping it.
    """

    if not raw:
        return ()
    compiled: list[UserPattern] = []
    for index, entry in enumerate(raw):
        name = entry.get("name") if isinstance(entry, dict) else None
        pattern_str = entry.get("pattern") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError(f"user pattern at index {index} missing or empty 'name'")
        if not isinstance(pattern_str, str) or not pattern_str:
            raise ValueError(f"user pattern {name!r} missing or empty 'pattern'")
        try:
            compiled.append(UserPattern(name=name, pattern=re.compile(pattern_str)))
        except re.error as err:
            raise ValueError(f"user pattern {name!r} is not a valid regex: {err}") from err
    return tuple(compiled)


def redact_value(value: Any, policy: RedactionPolicy) -> tuple[Any, RedactionStats]:
    """Recursively redact string leaves while preserving structure.

    Shared between the trajectory and session ingesters so a JSON-y
    blob (message content blocks, session metadata, nested config) is
    redacted with the same rules. Non-string scalars (int / float /
    bool / None) pass through unchanged.

    Returns ``(redacted_value, stats)``; ``stats`` aggregates the
    per-leaf redactions so callers can fold them into their own
    per-row or per-call totals without re-walking the structure.
    """
    stats = RedactionStats()
    out = _walk(value, policy=policy, stats=stats)
    return out, stats


def _walk(value: Any, *, policy: RedactionPolicy, stats: RedactionStats) -> Any:
    if isinstance(value, str):
        redacted, sub = redact_text(value, policy)
        for category, count in sub.by_category.items():
            stats.add(category, count)
        return redacted
    if isinstance(value, list):
        return [_walk(item, policy=policy, stats=stats) for item in value]
    if isinstance(value, dict):
        return {k: _walk(v, policy=policy, stats=stats) for k, v in value.items()}
    return value


__all__ = [
    "RedactionPolicy",
    "RedactionStats",
    "UserPattern",
    "compile_user_patterns",
    "redact_text",
    "redact_value",
]
