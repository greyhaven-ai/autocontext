"""Strip revision metadata from agent outputs.

LLM revision agents often prepend/append analysis headers, self-assessment,
and "Key Changes Made" sections alongside the actual revised content.
This inflates judge scores by mixing meta-commentary with the deliverable.
"""

from __future__ import annotations

import re


def _strip_markdown_fence_wrapper(text: str) -> str:
    """Strip a single outer markdown code fence around the whole output.

    Some LLM providers (notably claude-cli on Lean / Python prompts) return
    output wrapped in a single ``` ... ``` block, optionally tagged with a
    language: ``` ```lean ... ``` ```. Verifiers that compile the output
    directly (`lake env lean`, `mypy`, `cargo check`, ...) choke on the
    literal fence lines and reject otherwise-valid content. AC-754.

    The strip is conservative: only an outer wrapper that opens on the
    first non-blank line with ``` (optionally followed by a single language
    token) AND closes on the last non-blank line with ``` is removed.
    Unbalanced fences, inline triple-backticks, and nested code blocks
    inside an outer wrapper are preserved so we never silently mangle
    content the verifier might actually need.
    """
    stripped = text.strip()
    if not stripped:
        return text
    lines = stripped.splitlines()
    if len(lines) < 2:
        return text
    first = lines[0].rstrip()
    last = lines[-1].rstrip()
    if not first.startswith("```"):
        return text
    lang_token = first[3:]
    # The opening fence allows at most a single language tag (no whitespace).
    if lang_token and any(ch.isspace() for ch in lang_token):
        return text
    if last != "```":
        return text
    return "\n".join(lines[1:-1])


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
    - A single outer markdown code fence (e.g. ``` ```lean ... ``` ```) when
      the whole output is wrapped in one, so verifiers that compile the
      content directly do not choke on fence lines (AC-754).
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

    # AC-754: peel off an outer markdown fence wrapper after metadata
    # sections are gone, so a `## Revised Output` header above a fenced
    # block doesn't prevent the strip.
    cleaned = _strip_markdown_fence_wrapper(cleaned)

    return cleaned.strip()
