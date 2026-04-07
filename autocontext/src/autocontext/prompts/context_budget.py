"""Context budget management for prompt assembly.

Estimates token count per prompt component and progressively trims
when the total exceeds the configured budget. Trimming cascade order
(least critical first): trajectory, analysis, tools, lessons, playbook.
Hints are never trimmed -- they're the most actionable recent context.

Limitation: uses a char/4 heuristic for token estimation, not a real
tokenizer. Accurate enough for budget enforcement without adding a
dependency.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Trim cascade: first entry trimmed first (least critical)
_TRIM_ORDER = (
    "session_reports",
    "evidence_manifest",
    "notebook_architect",
    "notebook_coach",
    "notebook_analyst",
    "notebook_competitor",
    "experiment_log",
    "research_protocol",
    "environment_snapshot",
    "trajectory",
    "analysis",
    "tools",
    "lessons",
    "playbook",
)

# Components that are never trimmed
_PROTECTED = frozenset({"hints", "dead_ends"})


def estimate_tokens(text: str) -> int:
    """Estimate token count using char/4 heuristic."""
    return len(text) // 4


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens."""
    if max_tokens <= 0:
        return ""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars // 2:
        truncated = truncated[:last_nl]
    return truncated + "\n[... truncated for context budget ...]"


class ContextBudget:
    """Manages prompt context within a token budget.

    Applies a progressive trimming cascade when total estimated tokens
    exceed ``max_tokens``. Components are trimmed in order from least
    critical (trajectory) to most critical (playbook). Hints are never
    trimmed.
    """

    def __init__(self, max_tokens: int = 100_000) -> None:
        self.max_tokens = max_tokens

    def apply(self, components: dict[str, str]) -> dict[str, str]:
        """Apply budget to components dict, returning trimmed copy."""
        if self.max_tokens <= 0:
            return dict(components)

        total = sum(estimate_tokens(v) for v in components.values())
        if total <= self.max_tokens:
            return dict(components)

        logger.info(
            "context budget exceeded: %d estimated tokens > %d max, trimming",
            total,
            self.max_tokens,
        )

        result = dict(components)
        remaining = total
        for key in _TRIM_ORDER:
            if key not in result or key in _PROTECTED:
                continue
            if remaining <= self.max_tokens:
                break
            overshoot = remaining - self.max_tokens
            old_tokens = estimate_tokens(result[key])
            target_tokens = max(0, old_tokens - overshoot)
            if target_tokens < old_tokens:
                result[key] = _truncate_to_tokens(result[key], target_tokens)
                new_tokens = estimate_tokens(result[key])
                remaining -= old_tokens - new_tokens
                logger.debug(
                    "trimmed %s from %d to %d est. tokens",
                    key,
                    old_tokens,
                    new_tokens,
                )

        return result
