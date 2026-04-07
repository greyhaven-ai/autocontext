"""Review surface — highlights suspicious content for operator (AC-519).

Pure functions only — no interactive I/O. The CLI wrapper handles
user interaction; these functions identify what needs attention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class SuspiciousPattern:
    """A pattern in redacted content that may still warrant human review."""

    description: str
    line_number: int
    snippet: str  # up to 80 chars of context


# Patterns that survived redaction but still look suspicious
_SUSPICIOUS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("SSH key reference", re.compile(r"\.ssh|id_rsa|id_ed25519|authorized_keys", re.IGNORECASE)),
    ("Credential file reference", re.compile(r"credentials|\.aws|\.kube|\.docker|\.netrc", re.IGNORECASE)),
    ("Secret/password variable", re.compile(r"(?:SECRET|PASSWORD|PASSWD|TOKEN|AUTH)[\s_]*[=:]", re.IGNORECASE)),
    ("Private key marker", re.compile(r"BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY", re.IGNORECASE)),
    ("Base64 blob (>40 chars)", re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")),
    ("Connection string", re.compile(r"(?:postgres|mysql|mongodb|redis)://", re.IGNORECASE)),
]


def find_suspicious_patterns(text: str) -> list[SuspiciousPattern]:
    """Scan text for patterns that may warrant human review after redaction."""
    findings: list[SuspiciousPattern] = []
    for line_num, line in enumerate(text.split("\n"), 1):
        # Skip lines that are already fully redacted
        if line.strip().startswith("[REDACTED"):
            continue
        for description, pattern in _SUSPICIOUS_PATTERNS:
            if pattern.search(line):
                snippet = line[:80] + "..." if len(line) > 80 else line
                findings.append(
                    SuspiciousPattern(
                        description=description,
                        line_number=line_num,
                        snippet=snippet,
                    )
                )
    return findings


def generate_review_summary(
    total_files: int,
    redaction_count: int,
    suspicious_count: int,
    trufflehog_findings: int,
) -> str:
    """Generate a human-readable review summary."""
    lines = [
        "## Share Review Summary",
        f"- {total_files} files in export bundle",
        f"- {redaction_count} automatic redactions applied",
    ]
    if suspicious_count > 0:
        lines.append(f"- {suspicious_count} suspicious patterns flagged for review")
    else:
        lines.append("- No suspicious patterns found after redaction")

    if trufflehog_findings > 0:
        lines.append(f"- **{trufflehog_findings} TruffleHog findings** — review required before publication")
    else:
        lines.append("- TruffleHog scan clean")

    return "\n".join(lines)
