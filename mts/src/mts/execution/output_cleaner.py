"""Strip revision metadata from agent outputs.

LLM revision agents often prepend/append analysis headers, self-assessment,
and "Key Changes Made" sections alongside the actual revised content.
This inflates judge scores by mixing meta-commentary with the deliverable.
"""

from __future__ import annotations

import re


def _strip_last_section(text: str, header: str) -> str:
    """Strip from the last occurrence of *header* to the end of *text*.

    Only triggers when *header* appears at a newline boundary (or start of string).
    This avoids destroying legitimate content that may use the same header earlier.
    """
    # Check for header at start of string
    if text.startswith(header):
        return ""
    idx = text.rfind(f"\n{header}")
    if idx != -1:
        return text[:idx]
    return text


def clean_revision_output(output: str) -> str:
    """Remove common revision metadata patterns from LLM output.

    Strips:
    - ``## Revised Output`` header at the start
    - ``## Key Changes Made`` and everything after
    - ``**Analysis:**`` and everything after
    - ``## Analysis``, ``## Changes``, ``## Improvements``, ``## Self-Assessment`` sections
      (from the *last* occurrence only, to avoid destroying legitimate content)
    - Trailing "This revision transforms/improves/addresses/fixes..." paragraphs
    """
    cleaned = output

    # Strip "## Revised Output" header at the start
    cleaned = re.sub(r"^## Revised Output\s*\n", "", cleaned)

    # Unambiguous metadata headers — always strip from first occurrence
    unambiguous_patterns = [
        r"(?:^|\n)## Key Changes Made[\s\S]*",
        r"(?:^|\n)\*\*Analysis:\*\*[\s\S]*",
        r"(?:^|\n)## Self-Assessment[\s\S]*",
    ]
    for pattern in unambiguous_patterns:
        cleaned = re.sub(pattern, "", cleaned)

    # Ambiguous headers — only strip from the last occurrence to preserve
    # legitimate content that may use the same heading earlier
    for header in ("## Analysis", "## Changes", "## Improvements"):
        cleaned = _strip_last_section(cleaned, header)

    # Strip trailing meta-paragraphs starting with "This revision ..."
    cleaned = re.sub(
        r"(?:^|\n)This revision (?:transforms|improves|addresses|fixes)[\s\S]*$",
        "",
        cleaned,
    )

    return cleaned.strip()
