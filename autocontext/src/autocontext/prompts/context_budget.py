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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Trim cascade: first entry trimmed first (least critical)
_TRIM_ORDER = (
    "session_reports",
    "evidence_manifest",
    "evidence_manifest_analyst",
    "evidence_manifest_architect",
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

# Components that belong to separate final role prompts. They may share text
# without being duplicate context inside any one prompt.
_ROLE_SCOPED_COMPONENTS = frozenset(
    {
        "evidence_manifest_analyst",
        "evidence_manifest_architect",
        "notebook_competitor",
        "notebook_analyst",
        "notebook_coach",
        "notebook_architect",
    }
)

_TRUNCATION_MARKER = "\n[... truncated for context budget ...]"

_CANONICAL_COMPONENT_ORDER = (
    "hints",
    "dead_ends",
    "playbook",
    "lessons",
    "analysis",
    "trajectory",
    "tools",
    "session_reports",
    "research_protocol",
    "experiment_log",
    "environment_snapshot",
    "evidence_manifest",
    "evidence_manifest_analyst",
    "evidence_manifest_architect",
    "notebook_competitor",
    "notebook_analyst",
    "notebook_coach",
    "notebook_architect",
)

_COMPONENT_TOKEN_CAPS = {
    "playbook": 2800,
    "lessons": 1600,
    "analysis": 1800,
    "trajectory": 1200,
    "tools": 1400,
    "experiment_log": 1800,
    "research_protocol": 1200,
    "session_reports": 1400,
    "environment_snapshot": 1200,
    "evidence_manifest": 1200,
    "evidence_manifest_analyst": 1200,
    "evidence_manifest_architect": 1200,
    "notebook_competitor": 800,
    "notebook_analyst": 800,
    "notebook_coach": 800,
    "notebook_architect": 800,
}


@dataclass(frozen=True)
class ContextBudgetPolicy:
    """Domain policy for prompt component selection under context pressure."""

    trim_order: tuple[str, ...] = _TRIM_ORDER
    protected_components: frozenset[str] = _PROTECTED
    role_scoped_components: frozenset[str] = _ROLE_SCOPED_COMPONENTS
    component_token_caps: Mapping[str, int] = field(default_factory=lambda: dict(_COMPONENT_TOKEN_CAPS))
    canonical_component_order: tuple[str, ...] = _CANONICAL_COMPONENT_ORDER


def estimate_tokens(text: str) -> int:
    """Estimate token count using char/4 heuristic."""
    return len(text) // 4


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens."""
    if max_tokens <= 0:
        return ""
    max_chars = max_tokens * 4 + 3
    if len(text) <= max_chars:
        return text
    if len(_TRUNCATION_MARKER) > max_chars:
        return text[:max_chars]
    prefix_chars = max_chars - len(_TRUNCATION_MARKER)
    truncated = text[:prefix_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > prefix_chars // 2:
        truncated = truncated[:last_nl]
    return truncated + _TRUNCATION_MARKER


class ContextBudget:
    """Manages prompt context within a token budget.

    Applies a progressive trimming cascade when total estimated tokens
    exceed ``max_tokens``. Components are trimmed in order from least
    critical (trajectory) to most critical (playbook). Hints are never
    trimmed.
    """

    def __init__(
        self,
        max_tokens: int = 100_000,
        policy: ContextBudgetPolicy | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.policy = policy or ContextBudgetPolicy()

    def apply(self, components: dict[str, str]) -> dict[str, str]:
        """Apply budget to components dict, returning trimmed copy."""
        if self.max_tokens <= 0:
            return dict(components)

        result = _deduplicate_equivalent_components(dict(components), self.policy)
        result = _apply_component_caps(result, self.policy)

        total = sum(estimate_tokens(v) for v in result.values())
        if total <= self.max_tokens:
            return result

        logger.info(
            "context budget exceeded: %d estimated tokens > %d max, trimming",
            total,
            self.max_tokens,
        )

        remaining = total
        for key in self.policy.trim_order:
            if key not in result or key in self.policy.protected_components:
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


def _deduplicate_equivalent_components(
    components: dict[str, str],
    policy: ContextBudgetPolicy,
) -> dict[str, str]:
    groups: dict[str, list[str]] = {}
    for key, value in components.items():
        if key in policy.role_scoped_components:
            continue
        duplicate_key = _duplicate_key(value)
        if duplicate_key:
            groups.setdefault(duplicate_key, []).append(key)

    rank = _canonical_rank(policy.canonical_component_order)
    for keys in groups.values():
        if len(keys) < 2:
            continue
        unprotected = [key for key in keys if key not in policy.protected_components]
        if not unprotected:
            continue
        keep = min(unprotected, key=rank)
        for key in unprotected:
            if key != keep:
                components[key] = ""
                logger.debug("deduplicated prompt component %s in favor of %s", key, keep)
    return components


def _apply_component_caps(
    components: dict[str, str],
    policy: ContextBudgetPolicy,
) -> dict[str, str]:
    result = dict(components)
    for key, raw_cap in policy.component_token_caps.items():
        if key not in result or key in policy.protected_components:
            continue
        try:
            cap = int(raw_cap)
        except (TypeError, ValueError):
            continue
        value = result[key]
        if estimate_tokens(value) > cap:
            result[key] = _truncate_to_tokens(value, cap)
            logger.debug("capped prompt component %s at %d estimated tokens", key, cap)
    return result


def _duplicate_key(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized if normalized else ""


def _canonical_rank(order: tuple[str, ...]) -> Callable[[str], int]:
    order_rank = {key: index for index, key in enumerate(order)}

    def rank(key: str) -> int:
        return order_rank.get(key, len(order_rank))

    return rank
