"""Benchmark and observability helpers for semantic prompt compaction."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from autocontext.knowledge.compaction import compact_prompt_components, extract_promotable_lines
from autocontext.prompts.context_budget import ContextBudget, estimate_tokens

if TYPE_CHECKING:
    from collections.abc import Mapping

    from autocontext.prompts.templates import PromptBundle


@dataclass(slots=True, frozen=True)
class CompactionComponentBenchmark:
    """Per-component comparison of semantic compaction vs budget-only trimming."""

    name: str
    raw_tokens: int
    semantic_tokens: int
    budget_only_tokens: int
    semantic_budgeted_tokens: int
    signal_lines: list[str] = field(default_factory=list)
    semantic_signal_lines_preserved: int = 0
    budget_only_signal_lines_preserved: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(slots=True, frozen=True)
class PromptVariantBenchmark:
    """Prompt-level metrics for one build variant."""

    name: str
    context_tokens: int
    total_prompt_tokens: int
    role_prompt_tokens: dict[str, int]
    build_latency_ms: float
    signal_lines_preserved: int

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(slots=True, frozen=True)
class RegressionCheck:
    """Boolean quality/regression check captured in the benchmark report."""

    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(slots=True, frozen=True)
class SemanticCompactionBenchmarkReport:
    """Persistable benchmark report for semantic compaction vs budget-only trimming."""

    scenario_name: str
    run_id: str
    generation: int
    context_budget_tokens: int
    raw_context_tokens: int
    semantic_variant: PromptVariantBenchmark
    budget_only_variant: PromptVariantBenchmark
    components: list[CompactionComponentBenchmark]
    evidence_cache_hits: int = 0
    evidence_cache_lookups: int = 0
    regression_checks: list[RegressionCheck] = field(default_factory=list)

    @property
    def evidence_cache_hit_rate(self) -> float:
        if self.evidence_cache_lookups <= 0:
            return 0.0
        return round(self.evidence_cache_hits / self.evidence_cache_lookups, 4)

    def to_dict(self) -> dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["evidence_cache_hit_rate"] = self.evidence_cache_hit_rate
        return payload


def build_semantic_compaction_benchmark_report(
    *,
    scenario_name: str,
    run_id: str,
    generation: int,
    context_budget_tokens: int,
    raw_components: Mapping[str, str],
    semantic_prompts: PromptBundle,
    budget_only_prompts: PromptBundle,
    semantic_build_latency_ms: float,
    budget_only_build_latency_ms: float,
    evidence_cache_hits: int = 0,
    evidence_cache_lookups: int = 0,
) -> SemanticCompactionBenchmarkReport:
    """Compare semantic compaction against budget-only trimming on the same inputs."""
    filtered_components = {key: value for key, value in raw_components.items() if value}
    semantic_components = compact_prompt_components(filtered_components)
    budget_only_components = _apply_budget(filtered_components, context_budget_tokens)
    semantic_budgeted_components = _apply_budget(semantic_components, context_budget_tokens)

    components = [
        _build_component_benchmark(
            name=key,
            raw_text=filtered_components[key],
            semantic_text=semantic_components.get(key, filtered_components[key]),
            budget_only_text=budget_only_components.get(key, filtered_components[key]),
            semantic_budgeted_text=semantic_budgeted_components.get(key, semantic_components.get(key, filtered_components[key])),
        )
        for key in sorted(filtered_components)
    ]
    semantic_signal_lines = sum(item.semantic_signal_lines_preserved for item in components)
    budget_only_signal_lines = sum(item.budget_only_signal_lines_preserved for item in components)

    semantic_variant = PromptVariantBenchmark(
        name="semantic_compaction",
        context_tokens=_total_tokens(semantic_budgeted_components),
        total_prompt_tokens=_bundle_total_tokens(semantic_prompts),
        role_prompt_tokens=_bundle_role_tokens(semantic_prompts),
        build_latency_ms=round(semantic_build_latency_ms, 3),
        signal_lines_preserved=semantic_signal_lines,
    )
    budget_only_variant = PromptVariantBenchmark(
        name="budget_only",
        context_tokens=_total_tokens(budget_only_components),
        total_prompt_tokens=_bundle_total_tokens(budget_only_prompts),
        role_prompt_tokens=_bundle_role_tokens(budget_only_prompts),
        build_latency_ms=round(budget_only_build_latency_ms, 3),
        signal_lines_preserved=budget_only_signal_lines,
    )

    regression_checks = [
        RegressionCheck(
            name="signal_preservation_non_regression",
            passed=semantic_signal_lines >= budget_only_signal_lines,
            detail=(
                f"semantic preserved {semantic_signal_lines} signal lines; "
                f"budget-only preserved {budget_only_signal_lines}"
            ),
        ),
        RegressionCheck(
            name="semantic_context_non_expansive",
            passed=semantic_variant.context_tokens <= _total_tokens(filtered_components),
            detail=(
                f"semantic context tokens {semantic_variant.context_tokens}; "
                f"raw context tokens {_total_tokens(filtered_components)}"
            ),
        ),
        RegressionCheck(
            name="evidence_cache_accounting_valid",
            passed=evidence_cache_hits <= evidence_cache_lookups,
            detail=(
                f"cache hits {evidence_cache_hits}; "
                f"cache lookups {evidence_cache_lookups}"
            ),
        ),
    ]

    return SemanticCompactionBenchmarkReport(
        scenario_name=scenario_name,
        run_id=run_id,
        generation=generation,
        context_budget_tokens=context_budget_tokens,
        raw_context_tokens=_total_tokens(filtered_components),
        semantic_variant=semantic_variant,
        budget_only_variant=budget_only_variant,
        components=components,
        evidence_cache_hits=evidence_cache_hits,
        evidence_cache_lookups=evidence_cache_lookups,
        regression_checks=regression_checks,
    )


def _build_component_benchmark(
    *,
    name: str,
    raw_text: str,
    semantic_text: str,
    budget_only_text: str,
    semantic_budgeted_text: str,
) -> CompactionComponentBenchmark:
    signal_lines = extract_promotable_lines(raw_text)
    return CompactionComponentBenchmark(
        name=name,
        raw_tokens=estimate_tokens(raw_text),
        semantic_tokens=estimate_tokens(semantic_text),
        budget_only_tokens=estimate_tokens(budget_only_text),
        semantic_budgeted_tokens=estimate_tokens(semantic_budgeted_text),
        signal_lines=signal_lines,
        semantic_signal_lines_preserved=_count_preserved_signal_lines(signal_lines, semantic_budgeted_text),
        budget_only_signal_lines_preserved=_count_preserved_signal_lines(signal_lines, budget_only_text),
    )


def _apply_budget(components: Mapping[str, str], context_budget_tokens: int) -> dict[str, str]:
    if context_budget_tokens <= 0:
        return dict(components)
    return ContextBudget(max_tokens=context_budget_tokens).apply(dict(components))


def _total_tokens(components: Mapping[str, str]) -> int:
    return sum(estimate_tokens(value) for value in components.values())


def _bundle_role_tokens(bundle: PromptBundle) -> dict[str, int]:
    return {
        "competitor": estimate_tokens(bundle.competitor),
        "analyst": estimate_tokens(bundle.analyst),
        "coach": estimate_tokens(bundle.coach),
        "architect": estimate_tokens(bundle.architect),
    }


def _bundle_total_tokens(bundle: PromptBundle) -> int:
    return sum(_bundle_role_tokens(bundle).values())


def _count_preserved_signal_lines(signal_lines: list[str], text: str) -> int:
    normalized_text = _normalize(text)
    return sum(
        1
        for line in signal_lines
        if (normalized_line := _normalize(line)) and normalized_line in normalized_text
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()
