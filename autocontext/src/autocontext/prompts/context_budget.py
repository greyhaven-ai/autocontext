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
from typing import Any

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
    "fixtures",
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
    "fixtures",
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
    "fixtures": 800,
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


@dataclass(frozen=True)
class ContextBudgetTelemetry:
    """Structured telemetry for one prompt budget application."""

    max_tokens: int
    input_token_estimate: int
    output_token_estimate: int
    component_tokens_before: dict[str, int]
    component_tokens_after: dict[str, int]
    deduplicated_components: tuple[str, ...] = ()
    role_scoped_dedupe_skip_count: int = 0
    protected_dedupe_skip_count: int = 0
    component_cap_hits: tuple[dict[str, Any], ...] = ()
    global_trim_hits: tuple[dict[str, Any], ...] = ()

    @property
    def dedupe_hit_count(self) -> int:
        return len(self.deduplicated_components)

    @property
    def component_cap_hit_count(self) -> int:
        return len(self.component_cap_hits)

    @property
    def trimmed_components(self) -> tuple[str, ...]:
        return tuple(str(hit.get("component", "")) for hit in self.global_trim_hits if hit.get("component"))

    @property
    def trimmed_component_count(self) -> int:
        return len(self.global_trim_hits)

    @property
    def token_reduction(self) -> int:
        return max(0, self.input_token_estimate - self.output_token_estimate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "input_token_estimate": self.input_token_estimate,
            "output_token_estimate": self.output_token_estimate,
            "token_reduction": self.token_reduction,
            "component_tokens_before": dict(self.component_tokens_before),
            "component_tokens_after": dict(self.component_tokens_after),
            "dedupe_hit_count": self.dedupe_hit_count,
            "deduplicated_components": list(self.deduplicated_components),
            "role_scoped_dedupe_skip_count": self.role_scoped_dedupe_skip_count,
            "protected_dedupe_skip_count": self.protected_dedupe_skip_count,
            "component_cap_hit_count": self.component_cap_hit_count,
            "component_cap_hits": [dict(hit) for hit in self.component_cap_hits],
            "trimmed_component_count": self.trimmed_component_count,
            "trimmed_components": list(self.trimmed_components),
            "global_trim_hits": [dict(hit) for hit in self.global_trim_hits],
        }


@dataclass(frozen=True)
class ContextBudgetResult:
    """Budgeted prompt components plus decision telemetry."""

    components: dict[str, str]
    telemetry: ContextBudgetTelemetry


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
        return self.apply_with_telemetry(components).components

    def apply_with_telemetry(self, components: dict[str, str]) -> ContextBudgetResult:
        """Apply budget and return telemetry describing the reduction path."""
        input_components = dict(components)
        before_tokens = _component_token_counts(input_components)
        input_total = sum(before_tokens.values())
        if self.max_tokens <= 0:
            telemetry = ContextBudgetTelemetry(
                max_tokens=self.max_tokens,
                input_token_estimate=input_total,
                output_token_estimate=input_total,
                component_tokens_before=before_tokens,
                component_tokens_after=dict(before_tokens),
            )
            return ContextBudgetResult(components=input_components, telemetry=telemetry)

        result, deduplicated, role_scoped_skips, protected_skips = _deduplicate_equivalent_components(
            input_components,
            self.policy,
        )
        result, cap_hits = _apply_component_caps(result, self.policy)

        total = sum(estimate_tokens(v) for v in result.values())
        trim_hits: list[dict[str, Any]] = []
        remaining = total
        if total > self.max_tokens:
            logger.info(
                "context budget exceeded: %d estimated tokens > %d max, trimming",
                total,
                self.max_tokens,
            )

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
                    trim_hits.append(
                        {
                            "component": key,
                            "before_tokens": old_tokens,
                            "after_tokens": new_tokens,
                            "target_tokens": target_tokens,
                        }
                    )
                    logger.debug(
                        "trimmed %s from %d to %d est. tokens",
                        key,
                        old_tokens,
                        new_tokens,
                    )

        after_tokens = _component_token_counts(result)
        telemetry = ContextBudgetTelemetry(
            max_tokens=self.max_tokens,
            input_token_estimate=input_total,
            output_token_estimate=sum(after_tokens.values()),
            component_tokens_before=before_tokens,
            component_tokens_after=after_tokens,
            deduplicated_components=tuple(deduplicated),
            role_scoped_dedupe_skip_count=role_scoped_skips,
            protected_dedupe_skip_count=protected_skips,
            component_cap_hits=tuple(cap_hits),
            global_trim_hits=tuple(trim_hits),
        )
        return ContextBudgetResult(components=result, telemetry=telemetry)


def _deduplicate_equivalent_components(
    components: dict[str, str],
    policy: ContextBudgetPolicy,
) -> tuple[dict[str, str], list[str], int, int]:
    groups: dict[str, list[str]] = {}
    role_scoped_skips = 0
    for key, value in components.items():
        if key in policy.role_scoped_components:
            if _duplicate_key(value):
                role_scoped_skips += 1
            continue
        duplicate_key = _duplicate_key(value)
        if duplicate_key:
            groups.setdefault(duplicate_key, []).append(key)

    rank = _canonical_rank(policy.canonical_component_order)
    deduplicated: list[str] = []
    protected_skips = 0
    for keys in groups.values():
        if len(keys) < 2:
            continue
        protected_skips += sum(1 for key in keys if key in policy.protected_components)
        unprotected = [key for key in keys if key not in policy.protected_components]
        if not unprotected:
            continue
        keep = min(unprotected, key=rank)
        for key in unprotected:
            if key != keep:
                components[key] = ""
                deduplicated.append(key)
                logger.debug("deduplicated prompt component %s in favor of %s", key, keep)
    return components, deduplicated, role_scoped_skips, protected_skips


def _apply_component_caps(
    components: dict[str, str],
    policy: ContextBudgetPolicy,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    result = dict(components)
    hits: list[dict[str, Any]] = []
    for key, raw_cap in policy.component_token_caps.items():
        if key not in result or key in policy.protected_components:
            continue
        try:
            cap = int(raw_cap)
        except (TypeError, ValueError):
            continue
        value = result[key]
        old_tokens = estimate_tokens(value)
        if old_tokens > cap:
            result[key] = _truncate_to_tokens(value, cap)
            new_tokens = estimate_tokens(result[key])
            hits.append(
                {
                    "component": key,
                    "before_tokens": old_tokens,
                    "after_tokens": new_tokens,
                    "cap_tokens": cap,
                }
            )
            logger.debug("capped prompt component %s at %d estimated tokens", key, cap)
    return result, hits


def _duplicate_key(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized if normalized else ""


def _component_token_counts(components: Mapping[str, str]) -> dict[str, int]:
    return {key: estimate_tokens(value) for key, value in components.items()}


def _canonical_rank(order: tuple[str, ...]) -> Callable[[str], int]:
    order_rank = {key: index for index, key in enumerate(order)}

    def rank(key: str) -> int:
        return order_rank.get(key, len(order_rank))

    return rank
