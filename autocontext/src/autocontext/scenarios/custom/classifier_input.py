"""Shared normalization helpers for scenario-family classification inputs."""
from __future__ import annotations

import re

_CLASSIFIER_DESCRIPTION_SKIP_SECTIONS = frozenset(
    {
        "Why This Matters",
        "What This Tests",
        "Implementation Guidance",
        "Acceptance",
        "Why existing scenarios don't cover this",
        "Dependencies",
    }
)
_CLASSIFIER_DESCRIPTION_SKIP_LINE_PREFIXES = (
    "**Priority:**",
    "**Generations to signal:**",
)
_CLASSIFIER_INLINE_EXAMPLE_PAREN_RE = re.compile(
    r"\(\s*(?:e\.g\.,?|eg,?|for example,?)[^)]*\)",
    re.IGNORECASE,
)


def build_family_classification_brief(description: str) -> str:
    """Return the normalized description shared by live family-routing paths."""
    lines: list[str] = []
    skipping_section = False
    for raw_line in description.splitlines():
        heading_match = re.match(r"^\s*#{2,6}\s+(.+?)\s*$", raw_line)
        if heading_match is not None:
            title = heading_match.group(1).strip()
            skipping_section = title in _CLASSIFIER_DESCRIPTION_SKIP_SECTIONS
            if not skipping_section:
                lines.append(raw_line)
            continue

        stripped = raw_line.strip()
        if stripped.startswith(_CLASSIFIER_DESCRIPTION_SKIP_LINE_PREFIXES):
            continue
        if not skipping_section:
            lines.append(raw_line)

    brief = "\n".join(lines).strip()
    brief = _CLASSIFIER_INLINE_EXAMPLE_PAREN_RE.sub("", brief)
    brief = re.sub(r"\n{3,}", "\n\n", brief)
    brief = re.sub(r"[ \t]{2,}", " ", brief)
    return brief or description.strip()
