"""Natural-language scenario-family inference and routing (AC-246).

Classifies a natural-language description into a known scenario family
before spec generation, returning ranked choices with confidence and
rationale. Routes into the correct family-specific generator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from autocontext.scenarios.families import ScenarioFamily, get_family, list_families

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FamilyCandidate:
    """A ranked alternative family choice."""

    family_name: str
    confidence: float
    rationale: str


@dataclass(slots=True)
class FamilyClassification:
    """Result of classifying a description into a scenario family."""

    family_name: str
    confidence: float
    rationale: str
    alternatives: list[FamilyCandidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_name": self.family_name,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "alternatives": [
                {"family_name": a.family_name, "confidence": a.confidence, "rationale": a.rationale}
                for a in self.alternatives
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FamilyClassification:
        return cls(
            family_name=data["family_name"],
            confidence=data["confidence"],
            rationale=data["rationale"],
            alternatives=[
                FamilyCandidate(
                    family_name=a["family_name"],
                    confidence=a["confidence"],
                    rationale=a["rationale"],
                )
                for a in data.get("alternatives", [])
            ],
        )


class LowConfidenceError(Exception):
    """Raised when the top family classification is below the confidence threshold."""

    def __init__(self, classification: FamilyClassification, min_confidence: float) -> None:
        self.classification = classification
        self.min_confidence = min_confidence
        super().__init__(
            f"Family classification confidence {classification.confidence:.2f} "
            f"is below threshold {min_confidence:.2f} for family '{classification.family_name}'"
        )


# ---------------------------------------------------------------------------
# Keyword signal groups per family
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "by",
    "is", "are", "was", "be", "do", "does", "it", "we", "they", "i", "you",
    "that", "can", "should", "could", "would", "will", "must", "with", "which",
    "what", "how", "where", "when", "about", "create", "build", "make",
    "new", "need", "want", "scenario", "test", "testing",
})

# Simulation: stateful interaction with environment, action traces
_SIMULATION_SIGNALS: dict[str, float] = {
    "orchestrat": 2.0,  # orchestrate, orchestration
    "rollback": 2.0,
    "deploy": 1.5,
    "pipeline": 1.5,
    "workflow": 1.5,
    "incident": 1.5,
    "remediat": 1.5,  # remediate, remediation
    "triage": 1.5,
    "state machine": 2.0,
    "mock api": 2.0,
    "mock environment": 2.0,
    "api call": 1.5,
    "endpoint": 1.0,
    "microservice": 1.5,
    "service health": 1.5,
    "monitor": 1.0,
    "dashboard": 1.0,
    "recovery": 1.5,
    "failover": 2.0,
    "circuit breaker": 2.0,
    "retry": 1.0,
    "dependency order": 2.0,
    "correct order": 1.5,
    "action trace": 2.0,
    "side effect": 1.5,
    "transact": 1.5,  # transaction, transactional
    "simulat": 1.0,  # simulate, simulation
    "trace": 1.0,
    "step by step": 1.0,
    "health endpoint": 1.5,
    "server log": 1.0,
    "root cause": 1.0,
    "investigat": 1.0,  # investigate, investigation
}

# Agent task: evaluate output quality via LLM judge
_AGENT_TASK_SIGNALS: dict[str, float] = {
    "essay": 2.0,
    "article": 1.5,
    "blog": 1.5,
    "blog post": 2.0,
    "write about": 1.5,
    "persuasive": 1.5,
    "narrative": 1.0,
    "poem": 1.5,
    "haiku": 1.5,
    "story": 1.0,
    "fiction": 1.5,
    "prose": 1.5,
    "recipe": 1.5,
    "summariz": 1.5,  # summarize, summarization
    "abstract": 1.0,
    "generat": 1.0,  # generate, generation
    "translat": 1.5,  # translate, translation
    "classify": 1.0,
    "sentiment": 1.5,
    "report": 1.0,
    "review": 1.0,
    "evaluat": 1.0,  # evaluate, evaluation
    "code quality": 1.5,
    "python function": 1.5,
    "sort": 0.5,
    "data analysis": 1.0,
    "customer review": 1.0,
}

# Game: competitive, two-player, tournament, Elo
_GAME_SIGNALS: dict[str, float] = {
    "tournament": 2.0,
    "board game": 2.0,
    "compet": 1.5,  # compete, competitive, competition
    "two-player": 2.0,
    "two player": 2.0,
    "head-to-head": 2.0,
    "head to head": 2.0,
    "opponent": 1.5,
    "territory": 1.5,
    "capture the flag": 2.0,
    "grid game": 2.0,
    "maze": 1.0,
    "strategy game": 2.0,
    "resource management": 1.5,
    "scoring": 1.0,
    "elo": 2.0,
    "ranking": 1.0,
    "win": 0.5,
    "lose": 0.5,
    "match": 0.5,
    "player": 1.0,
}

_ARTIFACT_EDITING_SIGNALS: dict[str, float] = {
    "edit file": 2.0,
    "modify file": 2.0,
    "update config": 2.0,
    "configuration": 1.5,
    "config file": 1.5,
    "yaml": 1.5,
    "json": 1.0,
    "schema": 1.5,
    "migration": 1.5,
    "manifest": 1.5,
    "patch": 1.0,
    "refactor config": 2.0,
    "fix config": 2.0,
    "artifact": 1.5,
    "file edit": 2.0,
    "rewrite": 1.0,
    "update policy": 1.5,
    "change file": 1.5,
    "modify yaml": 2.0,
    "modify json": 2.0,
    "config repair": 2.0,
    "repair schema": 2.0,
    "sql migration": 2.0,
    "dockerfile": 1.5,
}

_FAMILY_SIGNAL_GROUPS: dict[str, dict[str, float]] = {
    "simulation": _SIMULATION_SIGNALS,
    "agent_task": _AGENT_TASK_SIGNALS,
    "game": _GAME_SIGNALS,
    "artifact_editing": _ARTIFACT_EDITING_SIGNALS,
}

_DEFAULT_FAMILY_NAME = "agent_task"


def _extract_words(text: str) -> list[str]:
    """Extract lowercase words, excluding stop words."""
    words = re.sub(r"[^a-z0-9\s\-]", " ", text.lower()).split()
    return [w for w in words if w not in _STOP_WORDS and len(w) > 1]


def _score_signals(text_lower: str, signals: dict[str, float]) -> tuple[float, list[str]]:
    """Score text against a signal dictionary. Returns (score, matched_signals)."""
    score = 0.0
    matched: list[str] = []
    for signal, weight in signals.items():
        if signal in text_lower:
            score += weight
            matched.append(signal)
    return score, matched


def _build_rationale(matched: list[str], family_name: str) -> str:
    if not matched:
        return f"No strong signals for {family_name}"
    top = matched[:3]
    return f"Matched {family_name} signals: {', '.join(top)}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_scenario_family(description: str) -> FamilyClassification:
    """Classify a natural-language description into a scenario family.

    Returns a FamilyClassification with the top choice, confidence,
    rationale, and ranked alternatives.

    Raises ValueError if description is empty/whitespace.
    """
    if not description or not description.strip():
        raise ValueError("description must be non-empty")

    text_lower = description.lower()
    registered_families = [family.name for family in list_families()]
    if not registered_families:
        raise ValueError("no scenario families are registered")

    raw_scores: dict[str, float] = {}
    matched_signals: dict[str, list[str]] = {}
    for family_name in registered_families:
        score, matched = _score_signals(text_lower, _FAMILY_SIGNAL_GROUPS.get(family_name, {}))
        raw_scores[family_name] = score
        matched_signals[family_name] = matched

    total = sum(raw_scores.values())
    if total == 0:
        # No signals matched — default to agent_task with low confidence if available.
        default_family = (
            _DEFAULT_FAMILY_NAME
            if _DEFAULT_FAMILY_NAME in registered_families
            else registered_families[0]
        )
        alternatives = [
            FamilyCandidate(
                family_name=family_name,
                confidence=0.1,
                rationale=f"No {family_name} signals",
            )
            for family_name in registered_families
            if family_name != default_family
        ]
        return FamilyClassification(
            family_name=default_family,
            confidence=0.2,
            rationale=f"No strong signals detected; defaulting to {default_family}",
            alternatives=alternatives,
        )

    # Normalize to confidences
    confidences = {name: score / total for name, score in raw_scores.items()}

    # Rank by confidence
    ranked = sorted(confidences.items(), key=lambda x: x[1], reverse=True)
    top_name, top_conf = ranked[0]

    alternatives = [
        FamilyCandidate(
            family_name=name,
            confidence=round(conf, 4),
            rationale=_build_rationale(matched_signals[name], name),
        )
        for name, conf in ranked[1:]
    ]

    return FamilyClassification(
        family_name=top_name,
        confidence=round(top_conf, 4),
        rationale=_build_rationale(matched_signals[top_name], top_name),
        alternatives=alternatives,
    )


def route_to_family(
    classification: FamilyClassification,
    min_confidence: float = 0.3,
) -> ScenarioFamily:
    """Route a classification to its ScenarioFamily.

    Raises LowConfidenceError if confidence < min_confidence.
    Raises KeyError if family_name is not in the registry.
    """
    if classification.confidence < min_confidence:
        raise LowConfidenceError(classification, min_confidence)
    return get_family(classification.family_name)
