"""Multi-layer content redaction for session sharing (AC-519).

Redaction layers (applied in order):
1. API key patterns (Anthropic, OpenAI, AWS, GitHub, Slack, generic)
2. PII patterns (emails, IP addresses)
3. Env-file value patterns (KEY=value on same line)
4. Absolute path stripping
5. High-risk file references (.ssh, .env contents, kube configs)

TruffleHog backstop runs separately via security/scanner.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Redaction patterns
# ---------------------------------------------------------------------------

_API_KEY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic", re.compile(r"sk-ant-[a-zA-Z0-9\-_]{20,}")),
    ("openai", re.compile(r"sk-(?:proj-)?[a-zA-Z0-9]{20,}")),
    ("aws_access", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github", re.compile(r"gh[ps]_[A-Za-z0-9_]{20,}")),
    ("slack_bot", re.compile(r"xoxb-[0-9A-Za-z\-]+")),
    ("slack_user", re.compile(r"xoxp-[0-9A-Za-z\-]+")),
    ("generic_bearer", re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)),
]

_PII_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("email", re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[REDACTED_EMAIL]"),
    ("ip_address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
]

_ENV_VALUE_PATTERN = re.compile(
    r"^(\s*[A-Z_][A-Z0-9_]*\s*=\s*)(.+)$",
    re.MULTILINE,
)

_ABSOLUTE_PATH_PATTERN = re.compile(r"(?:/(?:Users|home|root|var|tmp)/[^\s,;:\"'`\]})]+)")

_HIGH_RISK_FILE_REFS = re.compile(
    r"(?:\.env|\.ssh/|id_rsa|id_ed25519|\.kube/config|\.docker/config|"
    r"credentials\.json|\.aws/credentials|\.netrc|authorized_keys)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Redaction:
    """A single redaction applied to content."""

    category: str
    original_preview: str  # first 20 chars
    replacement: str
    line_number: int | None = None


@dataclass(slots=True)
class RedactionReport:
    """Summary of all redactions applied to a piece of content."""

    redactions: list[Redaction] = field(default_factory=list)
    total_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_count": self.total_count,
            "redactions": [
                {
                    "category": r.category,
                    "original_preview": r.original_preview,
                    "replacement": r.replacement,
                    "line_number": r.line_number,
                }
                for r in self.redactions
            ],
        }


def redact_content(text: str) -> str:
    """Redact sensitive content. Returns cleaned text."""
    result, _ = redact_content_with_report(text)
    return result


def redact_content_with_report(text: str) -> tuple[str, RedactionReport]:
    """Redact sensitive content and return a report of what was changed."""
    report = RedactionReport()

    # 1. API keys
    for name, pattern in _API_KEY_PATTERNS:
        text = _apply_pattern(text, pattern, "[REDACTED_API_KEY]", f"api_key:{name}", report)

    # 2. PII
    for name, pattern, replacement in _PII_PATTERNS:
        text = _apply_pattern(text, pattern, replacement, name, report)

    # 3. Env-file values (KEY=value → KEY=[REDACTED_ENV_VALUE])
    text = _redact_env_values(text, report)

    # 4. Absolute paths
    text = _apply_pattern(text, _ABSOLUTE_PATH_PATTERN, "[REDACTED_PATH]", "path", report)

    # 5. High-risk file references — flag content around them
    text = _redact_high_risk_context(text, report)

    report.total_count = len(report.redactions)
    return text, report


def _apply_pattern(
    text: str,
    pattern: re.Pattern[str],
    replacement: str,
    category: str,
    report: RedactionReport,
) -> str:
    matches = list(pattern.finditer(text))
    for match in reversed(matches):
        original = match.group()
        preview = original[:20] + "..." if len(original) > 20 else original
        report.redactions.append(
            Redaction(
                category=category,
                original_preview=preview,
                replacement=replacement,
            )
        )
        text = text[: match.start()] + replacement + text[match.end() :]
    return text


def _redact_env_values(text: str, report: RedactionReport) -> str:
    """Redact values in KEY=value patterns."""

    def replacer(match: re.Match[str]) -> str:
        key_part = match.group(1)
        value_part = match.group(2).strip()
        if not value_part or value_part == "[REDACTED_API_KEY]":
            return match.group(0)
        report.redactions.append(
            Redaction(
                category="env_value",
                original_preview=value_part[:20] + "..." if len(value_part) > 20 else value_part,
                replacement="[REDACTED_ENV_VALUE]",
            )
        )
        return f"{key_part}[REDACTED_ENV_VALUE]"

    return _ENV_VALUE_PATTERN.sub(replacer, text)


def _redact_high_risk_context(text: str, report: RedactionReport) -> str:
    """Flag lines that reference high-risk files."""
    lines = text.split("\n")
    result_lines: list[str] = []
    for line in lines:
        if _HIGH_RISK_FILE_REFS.search(line):
            report.redactions.append(
                Redaction(
                    category="high_risk_file",
                    original_preview=line[:40] + "..." if len(line) > 40 else line,
                    replacement="[REDACTED_HIGH_RISK_FILE_REFERENCE]",
                )
            )
            result_lines.append("[REDACTED_HIGH_RISK_FILE_REFERENCE]")
        else:
            result_lines.append(line)
    return "\n".join(result_lines)
