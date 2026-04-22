"""Natural-language scenario-family inference and routing (AC-246).

Classifies a natural-language description into a known scenario family
before spec generation, returning ranked choices with confidence and
rationale. Routes into the correct family-specific generator.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from autocontext.agents.types import LlmFn
from autocontext.scenarios.families import ScenarioFamily, get_family, list_families

if TYPE_CHECKING:
    from autocontext.scenarios.custom.classifier_cache import ClassifierCache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FamilyCandidate:
    """A ranked alternative family choice."""

    family_name: str
    confidence: float
    rationale: str


class FamilyClassification(BaseModel):
    """Result of classifying a description into a scenario family."""

    family_name: str
    confidence: float
    rationale: str
    alternatives: list[FamilyCandidate] = Field(default_factory=list)
    no_signals_matched: bool = False
    llm_fallback_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FamilyClassification:
        return cls.model_validate(data)


class LowConfidenceError(Exception):
    """Raised when the top family classification is below the confidence threshold."""

    def __init__(self, classification: FamilyClassification, min_confidence: float) -> None:
        self.classification = classification
        self.min_confidence = min_confidence
        super().__init__(self._build_message(classification, min_confidence))

    @staticmethod
    def _build_message(
        classification: FamilyClassification, min_confidence: float
    ) -> str:
        conf = classification.confidence
        thr = min_confidence
        family = classification.family_name

        if classification.no_signals_matched:
            return (
                f"Family classification confidence {conf:.2f} < threshold {thr:.2f}: "
                f"no family keywords matched in description (fell back to {family}). "
                f"Consider rephrasing with domain keywords."
            )

        base = (
            f"Family classification confidence {conf:.2f} < threshold {thr:.2f} "
            f"for family {family!r}."
        )
        if classification.alternatives:
            top_two = classification.alternatives[:2]
            alts_str = ", ".join(
                f"{a.family_name}={a.confidence:.2f}" for a in top_two
            )
            return f"{base} Top alternatives: {alts_str}."
        return base


# ---------------------------------------------------------------------------
# Keyword signal groups per family
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "is",
        "are",
        "was",
        "be",
        "do",
        "does",
        "it",
        "we",
        "they",
        "i",
        "you",
        "that",
        "can",
        "should",
        "could",
        "would",
        "will",
        "must",
        "with",
        "which",
        "what",
        "how",
        "where",
        "when",
        "about",
        "create",
        "build",
        "make",
        "new",
        "need",
        "want",
        "scenario",
        "test",
        "testing",
    }
)

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
    "geopolit": 2.0,
    "national security": 2.0,
    "diplomat": 1.5,
    "public communication": 1.5,
    "alliance": 1.5,
    "multilateral": 1.5,
    "international crisis": 2.0,
    "international confrontation": 2.0,
    "hidden adversary": 2.0,
    "escalation threshold": 1.5,
    "statecraft": 1.5,
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
    # AC-571: finance / quant domain keywords
    "portfolio": 2.0,
    "macroeconomic": 2.0,
    "regime change": 2.0,
    "rebalance": 1.5,
    "volatility": 1.5,
    "allocation": 1.0,
    "quantitative": 1.0,
    "investment": 1.0,
    "financial": 1.0,
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

_INVESTIGATION_SIGNALS: dict[str, float] = {
    "investigat": 2.0,
    "evidence": 2.0,
    "red herring": 2.0,
    "clue": 1.5,
    "forensic": 1.5,
    "root cause": 1.5,
    "diagnos": 2.0,
    "hypothesis": 1.5,
    "log analysis": 1.5,
    "incident timeline": 1.5,
    "query logs": 1.5,
    "triangulate": 1.5,
}

_WORKFLOW_SIGNALS: dict[str, float] = {
    "transaction": 2.0,
    "workflow step": 2.0,
    "compensation": 2.0,
    "rollback": 1.5,
    "retry": 1.5,
    "side effect": 2.0,
    "order processing": 2.0,
    "payment": 1.5,
    "idempotent": 1.5,
    "reversible": 1.5,
    "fulfillment": 1.5,
    "approval workflow": 2.0,
    "multi-step transaction": 2.0,
}

_SCHEMA_EVOLUTION_SIGNALS: dict[str, float] = {
    "schema evolv": 2.0,
    "schema evolution": 2.0,
    "schema-evolution": 2.0,
    "schemaevolutioninterface": 2.5,
    "schemamutation": 2.5,
    "stale context": 2.0,
    "stale-assumption": 2.0,
    "schema migration": 2.0,
    "knowledge migration": 2.0,
    "breaking change": 2.0,
    "breaking mutation": 2.0,
    "schema version": 2.0,
    "field removed": 1.5,
    "field added": 1.5,
    "field renamed": 1.5,
    "field type": 1.5,
    "required field": 1.5,
    "context invalidat": 2.0,
    "stale assumption": 2.0,
    "data model change": 1.5,
    "schema drift": 1.5,
    "backwards compat": 1.5,
}

_TOOL_FRAGILITY_SIGNALS: dict[str, float] = {
    "tool drift": 2.0,
    "api contract": 2.0,
    "tool fragility": 2.0,
    "environment drift": 2.0,
    "broken tool": 2.0,
    "tool version": 1.5,
    "api change": 1.5,
    "response format change": 2.0,
    "tool adapt": 1.5,
    "tool break": 1.5,
    "contract drift": 2.0,
    "endpoint deprecat": 1.5,
    "api deprecat": 1.5,
    "tool failure": 1.5,
}

_NEGOTIATION_SIGNALS: dict[str, float] = {
    "negotiat": 2.0,  # negotiate, negotiation
    "hidden preference": 2.0,
    "batna": 2.0,
    "opponent model": 2.0,
    "adversarial": 1.5,
    "counter offer": 2.0,
    "counter-offer": 2.0,
    "bargain": 1.5,
    "deal": 1.0,
    "concession": 1.5,
    "reservation value": 2.0,
    "repeated round": 2.0,
    "strategy adapt": 1.5,
    "hidden state": 1.5,
    "offer accept": 1.5,
}

_OPERATOR_LOOP_SIGNALS: dict[str, float] = {
    "escalat": 2.0,  # escalate, escalation
    "clarification": 2.0,
    "operator": 1.5,
    "human-in-the-loop": 2.0,
    "human in the loop": 2.0,
    "judgment": 1.5,
    "over-escalat": 2.0,
    "under-escalat": 2.0,
    "consult": 1.5,
    "approval required": 1.5,
    "ask for help": 1.5,
    "when to escalate": 2.0,
    "triage judgment": 2.0,
    "autonomous vs": 1.5,
    "operator loop": 2.0,
}

_COORDINATION_SIGNALS: dict[str, float] = {
    "coordinat": 2.0,  # coordinate, coordination
    "multi-agent": 2.0,
    "multi agent": 2.0,
    "worker": 1.5,
    "handoff": 2.0,
    "hand-off": 2.0,
    "partial context": 2.0,
    "merge output": 2.0,
    "merge result": 2.0,
    "duplication": 1.5,
    "parallel worker": 2.0,
    "context partition": 2.0,
    "split work": 1.5,
    "divide and conquer": 1.5,
    "task decompos": 1.5,
}

_FAMILY_SIGNAL_GROUPS: dict[str, dict[str, float]] = {
    "simulation": _SIMULATION_SIGNALS,
    "agent_task": _AGENT_TASK_SIGNALS,
    "game": _GAME_SIGNALS,
    "artifact_editing": _ARTIFACT_EDITING_SIGNALS,
    "investigation": _INVESTIGATION_SIGNALS,
    "workflow": _WORKFLOW_SIGNALS,
    "schema_evolution": _SCHEMA_EVOLUTION_SIGNALS,
    "tool_fragility": _TOOL_FRAGILITY_SIGNALS,
    "negotiation": _NEGOTIATION_SIGNALS,
    "operator_loop": _OPERATOR_LOOP_SIGNALS,
    "coordination": _COORDINATION_SIGNALS,
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
# LLM fallback (AC-580)
# ---------------------------------------------------------------------------


_LLM_FALLBACK_SYSTEM_PROMPT = (
    "You classify a natural-language scenario description into one of the "
    "registered scenario families. Respond with a single JSON object on one line: "
    '{{"family": "<name>", "confidence": <0.0-1.0>, "rationale": "<short explanation>"}}. '
    "The family name MUST be one of: {family_list}. Do not invent new family names."
)


def _llm_classify_fallback(
    description: str,
    registered_families: list[str],
    llm_fn: LlmFn,
    cache: ClassifierCache | None = None,
) -> FamilyClassification | None:
    """Single structured LLM call to classify a description. Returns None on any failure.

    When ``cache`` is provided (AC-581), the cache is consulted first and a
    successful result is written back on miss. Negative results (LLM raised,
    unparseable JSON, unknown family, etc.) are NOT cached — transient
    provider hiccups shouldn't poison future lookups.
    """
    import json

    if cache is not None:
        cached = cache.get(description, registered_families)
        if cached is not None:
            logger.info(
                "LLM classifier fallback: cache hit family=%s confidence=%.2f",
                cached.family_name,
                cached.confidence,
            )
            return cached

    family_list = ", ".join(registered_families)
    system = _LLM_FALLBACK_SYSTEM_PROMPT.format(family_list=family_list)

    try:
        raw = llm_fn(system, description)
    except Exception as exc:  # noqa: BLE001 - fallback must tolerate any provider failure
        logger.warning("LLM classifier fallback failed: llm_fn raised %s", exc)
        return None

    json_start = raw.find("{")
    json_end = raw.rfind("}")
    if json_start == -1 or json_end == -1 or json_end <= json_start:
        logger.warning("LLM classifier fallback failed: no JSON object in response")
        return None

    try:
        payload = json.loads(raw[json_start : json_end + 1])
    except json.JSONDecodeError as exc:
        logger.warning("LLM classifier fallback failed: JSON decode error %s", exc)
        return None

    family = payload.get("family") if isinstance(payload, dict) else None
    confidence = payload.get("confidence") if isinstance(payload, dict) else None
    rationale = payload.get("rationale") if isinstance(payload, dict) else None

    if not isinstance(family, str) or family not in registered_families:
        logger.warning("LLM classifier fallback failed: family %r not registered", family)
        return None
    if not isinstance(rationale, str) or not rationale.strip():
        logger.warning("LLM classifier fallback failed: missing rationale")
        return None
    try:
        conf_value = float(confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.warning("LLM classifier fallback failed: confidence %r not numeric", confidence)
        return None

    clamped = max(0.0, min(1.0, conf_value))
    logger.info("LLM classifier fallback: family=%s confidence=%.2f", family, clamped)

    alternatives = [
        FamilyCandidate(
            family_name=other,
            confidence=0.0,
            rationale="LLM fallback selected a different family",
        )
        for other in registered_families
        if other != family
    ]
    classification = FamilyClassification(
        family_name=family,
        confidence=round(clamped, 4),
        rationale=rationale,
        alternatives=alternatives,
        no_signals_matched=False,
        llm_fallback_used=True,
    )
    if cache is not None:
        cache.put(description, registered_families, classification)
    return classification


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_scenario_family(
    description: str,
    *,
    llm_fn: LlmFn | None = None,
    cache: ClassifierCache | None = None,
) -> FamilyClassification:
    """Classify a natural-language description into a scenario family.

    Returns a FamilyClassification with the top choice, confidence,
    rationale, and ranked alternatives.

    When ``llm_fn`` is provided and the keyword classifier matches no signals,
    a single structured LLM call is made to pick a family before returning the
    keyword fallback (AC-580). If the LLM call fails for any reason, the
    keyword fallback is returned unchanged.

    When ``cache`` is provided (AC-581), the LLM fallback consults the cache
    first and writes successful results back, eliminating repeat calls for
    the same description as long as the registered family set is stable.

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
        if llm_fn is not None:
            llm_result = _llm_classify_fallback(
                description, registered_families, llm_fn, cache=cache
            )
            if llm_result is not None:
                return llm_result
        # No signals matched — default to agent_task with low confidence if available.
        default_family = _DEFAULT_FAMILY_NAME if _DEFAULT_FAMILY_NAME in registered_families else registered_families[0]
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
            no_signals_matched=True,
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
        logger.warning(
            "route_to_family rejecting classification: family=%r confidence=%.2f threshold=%.2f rationale=%r alternatives=%s",
            classification.family_name,
            classification.confidence,
            min_confidence,
            classification.rationale,
            [(a.family_name, a.confidence) for a in classification.alternatives],
        )
        raise LowConfidenceError(classification, min_confidence)
    return get_family(classification.family_name)
