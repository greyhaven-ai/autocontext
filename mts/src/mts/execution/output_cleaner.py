"""Strip revision metadata from agent outputs.

LLM revision agents often prepend/append analysis headers, self-assessment,
and "Key Changes Made" sections alongside the actual revised content.
This inflates judge scores by mixing meta-commentary with the deliverable.
"""

from __future__ import annotations

import re


def clean_revision_output(output: str) -> str:
    """Remove common revision metadata patterns from LLM output.

    Strips:
    - ``## Revised Output`` header at the start
    - ``## Key Changes Made`` and everything after
    - ``**Analysis:**`` and everything after
    - ``## Analysis``, ``## Changes``, ``## Improvements``, ``## Self-Assessment`` sections
    - Trailing "This revision transforms/improves/addresses/fixes..." paragraphs
    """
    cleaned = output

    # Strip "## Revised Output" header at the start
    cleaned = re.sub(r"^## Revised Output\s*\n", "", cleaned)

    # Strip trailing sections — match at newline boundary or start of string
    trailing_patterns = [
        r"(?:^|\n)## Key Changes Made[\s\S]*",
        r"(?:^|\n)\*\*Analysis:\*\*[\s\S]*",
        r"(?:^|\n)## Analysis[\s\S]*",
        r"(?:^|\n)## Changes[\s\S]*",
        r"(?:^|\n)## Improvements[\s\S]*",
        r"(?:^|\n)## Self-Assessment[\s\S]*",
    ]

    for pattern in trailing_patterns:
        cleaned = re.sub(pattern, "", cleaned)

    # Strip trailing meta-paragraphs starting with "This revision ..."
    cleaned = re.sub(
        r"(?:^|\n)This revision (?:transforms|improves|addresses|fixes)[\s\S]*$",
        "",
        cleaned,
    )

    return cleaned.strip()
