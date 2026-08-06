"""Model-aware output-token clamp (AC-905).

A requested output budget must never exceed a model's known hard API
limit. The catalog lists only KNOWN hard caps; unknown models pass
through unclamped, because a stale catalog silently shrinking budgets is
worse than an occasional over-ask the provider rejects loudly.
"""

from __future__ import annotations

# known hard output-token limits by model-id prefix (longest prefix wins)
KNOWN_OUTPUT_CAPS: dict[str, int] = {
    "claude-3-haiku": 4096,
    "claude-3-opus": 4096,
    "claude-3-sonnet": 4096,
    "claude-3-5-haiku": 8192,
    "claude-3-5-sonnet": 8192,
}


def clamp_output_tokens(requested: int, model: str | None) -> int:
    """min(requested, known hard cap); unknown or absent models pass through."""
    if not model:
        return requested
    best_prefix = ""
    for prefix in KNOWN_OUTPUT_CAPS:
        if model.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix = prefix
    if not best_prefix:
        return requested
    return min(requested, KNOWN_OUTPUT_CAPS[best_prefix])
