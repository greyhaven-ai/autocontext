"""Strategy search — TF-IDF keyword matching over solved scenario knowledge."""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any
from weakref import WeakKeyDictionary

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
from autocontext.storage.artifacts import ArtifactStore

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
    terms = _tokenize(query)
    if not terms or top_k <= 0:
        return []
    index = _build_search_index(ctx)
    if not index:
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
        text_tokens = _field_tokens(text)
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


@lru_cache(maxsize=1024)
def _field_tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text))


@dataclass(frozen=True)
class _KnowledgeEntry:
    revision: tuple[tuple[object, ...], ...]
    playbook_excerpt: str
    lessons: str
    hints: str


# Scope cached contents to the artifact store lifetime and cap each store's
# entries. Check filesystem revisions on every query, including external edits.
_KNOWLEDGE_CACHE: WeakKeyDictionary[ArtifactStore, OrderedDict[str, _KnowledgeEntry]] = WeakKeyDictionary()
_CACHE_LOCK = RLock()


def _file_revision(path: Path) -> tuple[object, ...]:
    try:
        info = path.stat()
    except FileNotFoundError:
        return (str(path), None)
    return (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_search_knowledge(artifacts: ArtifactStore, name: str) -> _KnowledgeEntry:
    scenario_dir = artifacts._scenario_dir(name)
    paths = (
        scenario_dir / "playbook.md",
        artifacts._skill_dir(name) / "SKILL.md",
        scenario_dir / "hints.md",
        scenario_dir / "hint_state.json",
    )
    revision = tuple(_file_revision(path) for path in paths)
    with _CACHE_LOCK:
        cache = _KNOWLEDGE_CACHE.setdefault(artifacts, OrderedDict())
        cached = cache.get(name)
        if cached is not None and cached.revision == revision:
            cache.move_to_end(name)
            return cached
        entry = _KnowledgeEntry(
            revision=revision,
            playbook_excerpt=artifacts.read_playbook(name)[:500],
            lessons=" ".join(artifacts.read_skill_lessons_raw(name)),
            hints=artifacts.read_hints(name),
        )
        # Never retain a mixed revision if another process wrote during reads.
        if revision == tuple(_file_revision(path) for path in paths):
            cache[name] = entry
            cache.move_to_end(name)
            while len(cache) > 128:
                cache.popitem(last=False)
        else:
            cache.pop(name, None)
        return entry


def _build_search_index(ctx: MtsToolContext) -> list[dict[str, Any]]:
    """Build searchable entries for all scenarios with completed runs."""
    entries: list[dict[str, Any]] = []
    summary = ctx.sqlite.get_search_knowledge_summary()
    for name in sorted(SCENARIO_REGISTRY.keys() & summary.keys()):
        scenario = SCENARIO_REGISTRY[name]()
        snapshot = summary[name]
        knowledge = _read_search_knowledge(ctx.artifacts, name)

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
            "lessons": knowledge.lessons,
            "playbook_excerpt": knowledge.playbook_excerpt,
            "hints": knowledge.hints,
            "task_prompt": task_prompt,
            "judge_rubric": judge_rubric,
            "best_score": snapshot["best_score"],
            "best_elo": snapshot["best_elo"],
            "completed_runs": snapshot["completed_runs"],
        })
    return entries
