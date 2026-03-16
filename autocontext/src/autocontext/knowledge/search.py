"""Strategy search — TF-IDF keyword matching over solved scenario knowledge."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from autocontext.mcp.tools import MtsToolContext
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.capabilities import (
    get_description,
    get_evaluation_criteria,
    get_rubric_safe,
    get_strategy_interface_safe,
    get_task_prompt_safe,
    resolve_capabilities,
)

# Common English stopwords to ignore during search
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "and", "but", "or", "nor", "not", "so", "yet", "both",
    "each", "few", "more", "most", "other", "some", "such", "no", "only",
    "own", "same", "than", "too", "very", "just", "because", "about",
    "this", "that", "these", "those", "it", "its", "i", "me", "my",
    "we", "our", "you", "your", "he", "him", "his", "she", "her",
    "they", "them", "their", "what", "which", "who", "whom", "how",
    "when", "where", "why", "all", "any", "if", "up",
})


@dataclass(slots=True)
class SearchResult:
    scenario_name: str
    display_name: str
    description: str
    relevance_score: float
    best_score: float
    best_elo: float
    match_reason: str


def search_strategies(ctx: MtsToolContext, query: str, top_k: int = 5) -> list[SearchResult]:
    """Search solved scenarios by natural language query, ranked by keyword relevance."""
    index = _build_search_index(ctx)
    if not index:
        return []

    terms = _tokenize(query)
    if not terms:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in index:
        score, reasons = _keyword_score(terms, entry)
        if score > 0:
            scored.append((score, {**entry, "match_reason": "; ".join(reasons)}))

    scored.sort(key=lambda x: x[0], reverse=True)
    results: list[SearchResult] = []
    for relevance, entry in scored[:top_k]:
        results.append(SearchResult(
            scenario_name=entry["name"],
            display_name=entry["display_name"],
            description=entry["description"],
            relevance_score=min(relevance, 1.0),
            best_score=entry["best_score"],
            best_elo=entry["best_elo"],
            match_reason=entry["match_reason"],
        ))
    return results


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, remove stopwords."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in _STOPWORDS]


def _keyword_score(terms: list[str], entry: dict[str, Any]) -> tuple[float, list[str]]:
    """Compute weighted TF-IDF-style relevance score across entry fields."""
    field_weights: list[tuple[str, float]] = [
        ("name", 3.0),
        ("display_name", 3.0),
        ("description", 2.0),
        ("strategy_interface", 1.5),
        ("evaluation_criteria", 1.5),
        ("lessons", 1.5),
        ("playbook_excerpt", 1.0),
        ("hints", 1.0),
        ("task_prompt", 2.0),
        ("judge_rubric", 1.5),
    ]

    total = 0.0
    reasons: list[str] = []
    matched_terms: set[str] = set()

    for field_name, weight in field_weights:
        text = str(entry.get(field_name, "")).lower()
        if not text:
            continue
        text_tokens = set(re.findall(r"[a-z0-9]+", text))
        for term in terms:
            if term in text_tokens:
                total += weight
                matched_terms.add(term)
                if len(reasons) < 3:
                    reasons.append(f"'{term}' in {field_name}")

    # Normalize: divide by max possible score (all terms matched in all fields at max weight)
    max_possible = sum(w for _, w in field_weights) * len(terms)
    if max_possible > 0:
        total = total / max_possible

    # Boost for matching multiple distinct terms
    if len(matched_terms) > 1:
        coverage = len(matched_terms) / len(terms)
        total = total * (1.0 + 0.5 * coverage)

    return total, reasons


def _scenario_description(scenario: object) -> str:
    """Get description from either ScenarioInterface or AgentTaskInterface."""
    return get_description(scenario)


def _build_search_index(ctx: MtsToolContext) -> list[dict[str, Any]]:
    """Build searchable entries for all scenarios with completed runs."""
    entries: list[dict[str, Any]] = []
    for name in sorted(SCENARIO_REGISTRY.keys()):
        completed = ctx.sqlite.count_completed_runs(name)
        if completed == 0:
            continue

        scenario = SCENARIO_REGISTRY[name]()
        snapshot = ctx.sqlite.get_best_knowledge_snapshot(name)

        playbook = ctx.artifacts.read_playbook(name)
        playbook_excerpt = playbook[:500] if len(playbook) > 500 else playbook

        raw_lessons = ctx.artifacts.read_skill_lessons_raw(name)
        lessons_text = " ".join(raw_lessons)

        hints = ctx.artifacts.read_hints(name)

        caps = resolve_capabilities(scenario)
        strategy_interface = get_strategy_interface_safe(scenario) or ""
        evaluation_criteria = get_evaluation_criteria(scenario) if not caps.is_agent_task else ""
        task_prompt = get_task_prompt_safe(scenario) or ""
        judge_rubric = get_rubric_safe(scenario) or ""

        entries.append({
            "name": name,
            "display_name": name.replace("_", " ").title(),
            "description": _scenario_description(scenario),
            "strategy_interface": strategy_interface,
            "evaluation_criteria": evaluation_criteria,
            "lessons": lessons_text,
            "playbook_excerpt": playbook_excerpt,
            "hints": hints,
            "task_prompt": task_prompt,
            "judge_rubric": judge_rubric,
            "best_score": snapshot["best_score"] if snapshot else 0.0,
            "best_elo": snapshot["best_elo"] if snapshot else 1500.0,
            "completed_runs": completed,
        })
    return entries
