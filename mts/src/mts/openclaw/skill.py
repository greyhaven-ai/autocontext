"""ClawHub skill wrapper — high-level interface for MTS (AC-192)."""
from __future__ import annotations

import re
from typing import Any

from mts.knowledge.search import search_strategies
from mts.mcp.tools import (
    MtsToolContext,
    evaluate_strategy,
    list_artifacts,
    validate_strategy_against_harness,
)
from mts.openclaw.models import (
    ArtifactSummary,
    EvaluationResult,
    ScenarioInfo,
    ScenarioRecommendation,
    SkillManifest,
)
from mts.scenarios import SCENARIO_REGISTRY

# MCP tools exposed by the server — advertised in the manifest.
_MCP_TOOL_NAMES: list[str] = [
    "mts_list_scenarios",
    "mts_describe_scenario",
    "mts_validate_strategy",
    "mts_run_match",
    "mts_run_tournament",
    "mts_read_playbook",
    "mts_read_trajectory",
    "mts_read_analysis",
    "mts_read_hints",
    "mts_read_tools",
    "mts_read_skills",
    "mts_list_runs",
    "mts_run_status",
    "mts_run_replay",
    "mts_export_skill",
    "mts_list_solved",
    "mts_search_strategies",
    "mts_evaluate_strategy",
    "mts_validate_strategy_against_harness",
    "mts_publish_artifact",
    "mts_fetch_artifact",
    "mts_list_artifacts",
    "mts_capabilities",
    "mts_export_package",
    "mts_import_package",
    "mts_skill_manifest",
    "mts_skill_discover",
    "mts_skill_select",
    "mts_skill_evaluate",
    "mts_skill_discover_artifacts",
]

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "and", "or", "not", "this", "that", "these", "those", "it", "its", "how", "when", "where",
    "why", "what", "which",
})


def _build_scenario_info(name: str) -> ScenarioInfo:
    """Inspect a scenario from the registry and return ScenarioInfo."""
    instance = SCENARIO_REGISTRY[name]()

    if hasattr(instance, "describe_rules"):
        return ScenarioInfo(
            name=name,
            display_name=name.replace("_", " ").title(),
            scenario_type="game",
            description=instance.describe_rules()[:500],
            strategy_interface=instance.describe_strategy_interface() if hasattr(instance, "describe_strategy_interface") else "",
        )

    description = instance.describe_task()[:500] if hasattr(instance, "describe_task") else ""
    return ScenarioInfo(
        name=name,
        display_name=name.replace("_", " ").title(),
        scenario_type="agent_task",
        description=description,
        strategy_interface="",
    )


def _tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS]


def _registry_score(info: ScenarioInfo, query: str) -> tuple[float, str]:
    terms = _tokenize(query)
    if not terms:
        return 0.0, ""

    weighted_fields: list[tuple[str, float, str]] = [
        (info.name.lower(), 3.0, "name"),
        (info.display_name.lower(), 3.0, "display_name"),
        (info.description.lower(), 2.0, "description"),
        (info.strategy_interface.lower(), 1.5, "strategy_interface"),
    ]

    total = 0.0
    reasons: list[str] = []
    matched_terms: set[str] = set()
    for text, weight, field_name in weighted_fields:
        text_tokens = set(re.findall(r"[a-z0-9]+", text))
        for term in terms:
            if term in text_tokens:
                total += weight
                matched_terms.add(term)
                if len(reasons) < 3:
                    reasons.append(f"'{term}' in {field_name}")

    if not matched_terms:
        return 0.0, ""

    max_possible = sum(weight for _, weight, _ in weighted_fields) * len(terms)
    score = total / max_possible if max_possible > 0 else 0.0
    if len(matched_terms) > 1:
        score *= 1.0 + 0.5 * (len(matched_terms) / len(terms))
    return min(score, 1.0), "; ".join(reasons)


def _rank_scenarios(
    ctx: MtsToolContext,
    scenarios: list[ScenarioInfo],
    query: str,
) -> list[tuple[ScenarioInfo, float, str]]:
    solved_results = {r.scenario_name: r for r in search_strategies(ctx, query, top_k=len(scenarios))}
    ranked: list[tuple[ScenarioInfo, float, str]] = []
    for scenario in scenarios:
        local_score, local_reason = _registry_score(scenario, query)
        solved = solved_results.get(scenario.name)
        if solved is not None:
            score = solved.relevance_score
            reason = solved.match_reason or local_reason
        else:
            score = local_score
            reason = local_reason
        ranked.append((scenario, score, reason))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


class MtsSkillWrapper:
    """High-level ClawHub skill interface for MTS.

    Wraps the low-level MCP tool functions into cohesive workflows
    that external agents can invoke through ClawHub discovery.
    """

    def __init__(self, ctx: MtsToolContext) -> None:
        self.ctx = ctx

    def manifest(self) -> SkillManifest:
        """Build the skill manifest from the current scenario registry."""
        from mts import __version__

        scenarios = [_build_scenario_info(name) for name in sorted(SCENARIO_REGISTRY)]
        return SkillManifest(
            version=__version__,
            scenarios=scenarios,
            mcp_tools=list(_MCP_TOOL_NAMES),
        )

    def discover_scenarios(self, query: str | None = None) -> list[ScenarioInfo]:
        """List available scenarios, optionally ordered by search relevance."""
        all_scenarios = [_build_scenario_info(name) for name in sorted(SCENARIO_REGISTRY)]

        if not query:
            return all_scenarios
        return [scenario for scenario, _, _ in _rank_scenarios(self.ctx, all_scenarios, query)]

    def select_scenario(self, description: str) -> ScenarioRecommendation:
        """Recommend the best scenario for a problem description."""
        all_scenarios = [_build_scenario_info(name) for name in sorted(SCENARIO_REGISTRY)]
        ranked = _rank_scenarios(self.ctx, all_scenarios, description)
        if not ranked:
            # Fallback: return first scenario with zero confidence
            fallback = all_scenarios[0] if all_scenarios else None
            return ScenarioRecommendation(
                scenario_name=fallback.name if fallback else "",
                confidence=0.0,
                reasoning="No matching scenarios found; returning first available.",
                alternatives=all_scenarios[1:] if len(all_scenarios) > 1 else [],
            )
        best_scenario, best_score, best_reason = ranked[0]
        if best_score <= 0.0:
            return ScenarioRecommendation(
                scenario_name=best_scenario.name,
                confidence=0.0,
                reasoning="No matching scenarios found; returning first available.",
                alternatives=[scenario for scenario, _, _ in ranked[1:]],
            )

        return ScenarioRecommendation(
            scenario_name=best_scenario.name,
            confidence=min(best_score, 1.0),
            reasoning=best_reason,
            alternatives=[scenario for scenario, _, _ in ranked[1:5]],
        )

    def evaluate(
        self,
        scenario_name: str,
        strategy: dict[str, Any],
        num_matches: int = 3,
        seed_base: int = 42,
    ) -> EvaluationResult:
        """Full validate + evaluate workflow."""
        if scenario_name not in SCENARIO_REGISTRY:
            return EvaluationResult(
                scenario_name=scenario_name,
                strategy=strategy,
                valid=False,
                validation_errors=[f"Scenario '{scenario_name}' not found in registry"],
            )

        scenario = SCENARIO_REGISTRY[scenario_name]()
        if not hasattr(scenario, "execute_match"):
            return EvaluationResult(
                scenario_name=scenario_name,
                strategy=strategy,
                valid=False,
                validation_errors=[
                    f"Scenario '{scenario_name}' is an agent task scenario. "
                    "Use judge-based output evaluation instead of skill_evaluate()."
                ],
            )

        # Step 1: validate
        vr: dict[str, Any] = validate_strategy_against_harness(scenario_name, strategy, ctx=self.ctx)
        valid = bool(vr.get("valid", False))
        reason: str = str(vr.get("reason", ""))
        harness_passed: bool | None = vr.get("harness_passed")
        harness_errors: list[str] = vr.get("harness_errors", [])
        validation_errors: list[str] = [reason] if reason and not valid else []

        if not valid:
            return EvaluationResult(
                scenario_name=scenario_name,
                strategy=strategy,
                valid=False,
                validation_errors=validation_errors,
                harness_passed=harness_passed,
                harness_errors=harness_errors,
            )

        # Step 2: evaluate
        er: dict[str, Any] = evaluate_strategy(scenario_name, strategy, num_matches, seed_base)
        if "error" in er:
            return EvaluationResult(
                scenario_name=scenario_name,
                strategy=strategy,
                valid=False,
                validation_errors=[str(er["error"])],
                harness_passed=harness_passed,
                harness_errors=harness_errors,
            )
        return EvaluationResult(
            scenario_name=scenario_name,
            strategy=strategy,
            valid=True,
            harness_passed=harness_passed,
            harness_errors=harness_errors,
            scores=er.get("scores", []),
            mean_score=float(er.get("mean_score", 0.0)),
            best_score=float(er.get("best_score", 0.0)),
        )

    def discover_artifacts(
        self,
        scenario: str | None = None,
        artifact_type: str | None = None,
    ) -> list[ArtifactSummary]:
        """Find published artifacts with enriched metadata."""
        raw: list[dict[str, Any]] = list_artifacts(self.ctx, scenario=scenario, artifact_type=artifact_type)
        return [
            ArtifactSummary(
                id=str(a.get("id", "")),
                name=str(a.get("name", "")),
                artifact_type=str(a.get("artifact_type", "")),
                scenario=str(a.get("scenario", "")),
                version=int(a.get("version", 1)),
                tags=list(a.get("tags", [])),
                created_at=str(a.get("created_at", "")),
            )
            for a in raw
        ]
