from __future__ import annotations

import dataclasses
import importlib.util
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.family_pipeline import validate_for_family, validate_source_for_family
from autocontext.scenarios.custom.investigation_codegen import generate_investigation_class
from autocontext.scenarios.custom.investigation_spec import InvestigationSpec
from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel
from autocontext.scenarios.families import get_family_marker
from autocontext.scenarios.investigation import EvidenceItem
from autocontext.scenarios.simulation import Action
from autocontext.simulation.helpers import find_scenario_class
from autocontext.util.json_io import write_json


@dataclass(slots=True)
class InvestigationRequest:
    description: str
    max_steps: int | None = None
    max_hypotheses: int | None = None
    save_as: str | None = None


@dataclass(slots=True)
class InvestigationHypothesis:
    id: str
    statement: str
    status: str
    confidence: float


@dataclass(slots=True)
class InvestigationEvidence:
    id: str
    kind: str
    source: str
    summary: str
    supports: list[str] = field(default_factory=list)
    contradicts: list[str] = field(default_factory=list)
    is_red_herring: bool = False


@dataclass(slots=True)
class InvestigationConclusion:
    best_explanation: str
    confidence: float
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class InvestigationArtifacts:
    investigation_dir: str
    report_path: str | None = None


@dataclass(slots=True)
class InvestigationResult:
    id: str
    name: str
    family: str
    status: str
    description: str
    question: str
    hypotheses: list[InvestigationHypothesis]
    evidence: list[InvestigationEvidence]
    conclusion: InvestigationConclusion
    unknowns: list[str]
    recommended_next_steps: list[str]
    steps_executed: int
    artifacts: InvestigationArtifacts
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InvestigationResult:
        hypotheses = [InvestigationHypothesis(**item) for item in data.get("hypotheses", [])]
        evidence = [InvestigationEvidence(**item) for item in data.get("evidence", [])]
        conclusion = InvestigationConclusion(**data.get("conclusion", {}))
        artifacts = InvestigationArtifacts(**data.get("artifacts", {}))
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            family=str(data.get("family", "investigation")),
            status=str(data.get("status", "failed")),
            description=str(data.get("description", "")),
            question=str(data.get("question", "")),
            hypotheses=hypotheses,
            evidence=evidence,
            conclusion=conclusion,
            unknowns=[str(item) for item in data.get("unknowns", [])],
            recommended_next_steps=[str(item) for item in data.get("recommended_next_steps", [])],
            steps_executed=int(data.get("steps_executed", 0)),
            artifacts=artifacts,
            error=str(data["error"]) if data.get("error") is not None else None,
        )


@dataclass(slots=True)
class _ExecutedInvestigation:
    steps_executed: int
    collected_evidence: list[EvidenceItem]
    final_state: dict[str, Any]


def derive_investigation_name(description: str) -> str:
    words = re.sub(r"[^a-z0-9\s]", " ", description.lower()).split()
    return "_".join(word for word in words if len(word) > 2)[:80] or "investigation"


def generate_investigation_id() -> str:
    return f"inv_{uuid.uuid4().hex[:12]}"


def normalize_positive_integer(value: int | None) -> int | None:
    if value is None or value <= 0:
        return None
    return int(value)


def parse_investigation_json(text: str) -> dict[str, Any] | None:
    trimmed = text.strip()
    candidates = [trimmed]

    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", trimmed, re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())

    start = trimmed.find("{")
    end = trimmed.rfind("}")
    if start != -1 and end > start:
        candidates.append(trimmed[start : end + 1])

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _build_investigation_spec_prompt(description: str) -> tuple[str, str]:
    system_prompt = (
        "You are an investigation designer. Given a problem description, produce an investigation spec as JSON.\n\n"
        "Required fields:\n"
        "- description: investigation summary\n"
        "- environment_description: system/context being investigated\n"
        "- initial_state_description: what is known at the start\n"
        "- evidence_pool_description: what evidence sources are available, including any red herring\n"
        "- diagnosis_target: the root cause or diagnosis we are trying to determine\n"
        "- success_criteria: array of strings\n"
        "- failure_modes: array of strings\n"
        "- max_steps: positive integer\n"
        "- actions: array of {name, description, parameters, preconditions, effects}\n"
        "- when preconditions represent ordering, reference prior action names instead of environmental access assumptions\n\n"
        "Output ONLY the JSON object, no markdown fences."
    )
    return system_prompt, f"Investigation: {description}"


def _build_hypothesis_prompt(
    *,
    description: str,
    execution: _ExecutedInvestigation,
    max_hypotheses: int | None,
) -> tuple[str, str]:
    system_prompt = (
        "You are a diagnostic analyst. Given an investigation description and collected evidence, generate hypotheses. "
        "Output JSON with this shape:\n"
        "{\n"
        '  "question": "The specific question being investigated",\n'
        '  "hypotheses": [\n'
        '    { "statement": "Hypothesis text", "confidence": 0.0 }\n'
        "  ]\n"
        "}\n"
        "Output ONLY the JSON object."
    )
    evidence = ", ".join(item.content for item in execution.collected_evidence) or "none yet"
    user_prompt = (
        f"Investigation: {description}\n"
        f"Evidence collected: {evidence}\n"
        f"Steps taken: {execution.steps_executed}\n"
        f"Maximum hypotheses: {max_hypotheses or 5}"
    )
    return system_prompt, user_prompt


def _spec_from_dict(data: dict[str, Any]) -> InvestigationSpec:
    errors = validate_for_family("investigation", data)
    if errors:
        raise ValueError("; ".join(errors))

    actions = [
        SimulationActionSpecModel(
            name=str(raw["name"]),
            description=str(raw["description"]),
            parameters=raw.get("parameters", {}) if isinstance(raw, dict) else {},
            preconditions=raw.get("preconditions", []) if isinstance(raw, dict) else [],
            effects=raw.get("effects", []) if isinstance(raw, dict) else [],
        )
        for raw in data.get("actions", [])
        if isinstance(raw, dict)
    ]
    return InvestigationSpec(
        description=str(data["description"]),
        environment_description=str(data["environment_description"]),
        initial_state_description=str(data["initial_state_description"]),
        evidence_pool_description=str(data["evidence_pool_description"]),
        diagnosis_target=str(data["diagnosis_target"]),
        success_criteria=[str(item) for item in data.get("success_criteria", [])],
        failure_modes=[str(item) for item in data.get("failure_modes", [])],
        actions=actions,
        max_steps=int(data.get("max_steps", 10)),
    )


def _persist_investigation_artifacts(
    knowledge_root: Path,
    name: str,
    spec: dict[str, Any],
    source: str,
) -> Path:
    investigation_dir = knowledge_root / "_investigations" / name
    investigation_dir.mkdir(parents=True, exist_ok=True)
    write_json(investigation_dir / "spec.json", {"name": name, "family": "investigation", **spec})
    (investigation_dir / "scenario.py").write_text(source, encoding="utf-8")
    (investigation_dir / "scenario_type.txt").write_text(get_family_marker("investigation"), encoding="utf-8")
    return investigation_dir


def _execute_generated_investigation(
    *,
    source: str,
    name: str,
    max_steps: int | None,
) -> _ExecutedInvestigation:
    mod_name = f"autocontext._investigation_gen.{name}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_loader(mod_name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    exec(source, module.__dict__)  # noqa: S102
    sys.modules[mod_name] = module

    scenario_class = find_scenario_class(module)
    if scenario_class is None:
        raise ValueError("No investigation scenario class found")

    instance = scenario_class()
    state = instance.initial_state(42)
    limit = max_steps or getattr(instance, "max_steps", lambda: 8)()
    steps = 0

    while steps < limit:
        if instance.is_terminal(state):
            break
        actions = instance.get_available_actions(state)
        if not actions:
            break

        next_action: Action | None = None
        for candidate in actions:
            action = Action(name=candidate.name, parameters={})
            valid, _reason = instance.validate_action(state, action)
            if valid:
                next_action = action
                break
        if next_action is None:
            break

        result, next_state = instance.execute_action(state, next_action)
        state = next_state
        if result.success:
            steps += 1
        else:
            break

    evidence_pool = {item.id: item for item in instance.get_evidence_pool(state)}
    collected_ids = [str(item) for item in state.get("collected_evidence_ids", [])]
    collected = [evidence_pool[item_id] for item_id in collected_ids if item_id in evidence_pool]
    return _ExecutedInvestigation(
        steps_executed=steps,
        collected_evidence=collected,
        final_state=state,
    )


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text.lower())).strip()


def _tokenize(text: str) -> list[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "the",
        "to",
        "of",
        "for",
        "in",
        "on",
        "at",
        "by",
        "with",
        "after",
        "before",
        "from",
        "our",
        "your",
        "their",
        "is",
        "was",
        "were",
        "be",
        "this",
        "that",
    }
    return [token for token in _normalize_text(text).split(" ") if len(token) > 1 and token not in stopwords]


def _similarity_score(left: str, right: str) -> float:
    left_tokens = set(_tokenize(left))
    right_tokens = set(_tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    matches = sum(1 for token in left_tokens if token in right_tokens)
    return matches / max(len(left_tokens), len(right_tokens))


def _build_evidence(execution: _ExecutedInvestigation) -> list[InvestigationEvidence]:
    return [
        InvestigationEvidence(
            id=item.id,
            kind="red_herring" if item.is_red_herring else "observation",
            source=item.source,
            summary=item.content,
            is_red_herring=item.is_red_herring,
        )
        for item in execution.collected_evidence
    ]


def _parse_hypotheses(
    *,
    text: str,
    description: str,
    max_hypotheses: int | None,
) -> tuple[str, list[dict[str, Any]]]:
    parsed = parse_investigation_json(text)
    if not parsed or not isinstance(parsed.get("hypotheses"), list):
        return description, [
            {"statement": f"Investigate: {description}", "confidence": 0.5},
        ][: normalize_positive_integer(max_hypotheses) or 1]

    hypotheses = []
    for raw in parsed["hypotheses"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("statement"), str):
            continue
        confidence = raw.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        hypotheses.append(
            {
                "statement": str(raw["statement"]),
                "confidence": max(0.0, min(1.0, float(confidence))),
            }
        )

    limit = normalize_positive_integer(max_hypotheses)
    if limit is not None:
        hypotheses = hypotheses[:limit]
    return str(parsed.get("question") or description), hypotheses


def _evaluate_hypotheses(
    *,
    hypotheses: list[dict[str, Any]],
    evidence: list[InvestigationEvidence],
    diagnosis_target: str,
) -> tuple[list[InvestigationHypothesis], list[InvestigationEvidence]]:
    annotated_evidence = [
        InvestigationEvidence(
            id=item.id,
            kind=item.kind,
            source=item.source,
            summary=item.summary,
            supports=list(item.supports),
            contradicts=list(item.contradicts),
            is_red_herring=item.is_red_herring,
        )
        for item in evidence
    ]
    normalized_target = _normalize_text(diagnosis_target)
    evaluated: list[InvestigationHypothesis] = []

    for index, hypothesis in enumerate(hypotheses):
        hypothesis_id = f"h{index}"
        statement = str(hypothesis.get("statement", ""))
        confidence = float(hypothesis.get("confidence", 0.5))
        matches_target = bool(normalized_target) and _similarity_score(statement, normalized_target) >= 0.34
        supporting = 0.0
        contradicting = 0.0

        for item in annotated_evidence:
            overlap = _similarity_score(statement, item.summary)
            related = overlap >= 0.34
            if item.is_red_herring:
                if related:
                    item.contradicts.append(hypothesis_id)
                    contradicting += overlap
            elif related or matches_target:
                item.supports.append(hypothesis_id)
                supporting += max(overlap, 0.5 if matches_target else 0.0)

        status = "unresolved"
        if supporting > contradicting and supporting > 0:
            status = "supported"
        elif contradicting > supporting and contradicting > 0:
            status = "contradicted"

        evaluated.append(
            InvestigationHypothesis(
                id=hypothesis_id,
                statement=statement,
                status=status,
                confidence=max(0.0, min(1.0, confidence)),
            )
        )

    return evaluated, annotated_evidence


def _build_conclusion(
    hypotheses: list[InvestigationHypothesis],
    evidence: list[InvestigationEvidence],
) -> InvestigationConclusion:
    supported = sorted(
        [hypothesis for hypothesis in hypotheses if hypothesis.status == "supported"],
        key=lambda item: item.confidence,
        reverse=True,
    )
    best = supported[0] if supported else None
    limitations: list[str] = []
    red_herrings = sum(1 for item in evidence if item.is_red_herring)
    if red_herrings:
        limitations.append(f"{red_herrings} potential red herring(s) in evidence pool")
    if any(hypothesis.status == "unresolved" for hypothesis in hypotheses):
        limitations.append("Some hypotheses remain unresolved")
    limitations.append("Investigation based on generated scenario — not live system data")
    return InvestigationConclusion(
        best_explanation=best.statement if best else "No hypothesis received sufficient support",
        confidence=best.confidence if best else 0.0,
        limitations=limitations,
    )


def _identify_unknowns(
    hypotheses: list[InvestigationHypothesis],
    evidence: list[InvestigationEvidence],
) -> list[str]:
    unknowns = [
        f'Hypothesis "{hypothesis.statement}" needs more evidence'
        for hypothesis in hypotheses
        if hypothesis.status == "unresolved"
    ]
    if len(evidence) < 3:
        unknowns.append("Limited evidence collected — more data sources needed")
    return unknowns


def _recommend_next_steps(
    hypotheses: list[InvestigationHypothesis],
    unknowns: list[str],
) -> list[str]:
    steps: list[str] = []
    supported = [hypothesis for hypothesis in hypotheses if hypothesis.status == "supported"]
    if supported:
        steps.append(f'Verify leading hypothesis: "{supported[0].statement}"')
    for hypothesis in [item for item in hypotheses if item.status == "unresolved"][:2]:
        steps.append(f'Gather evidence for: "{hypothesis.statement}"')
    if unknowns:
        steps.append("Address identified unknowns before concluding")
    return steps


def _build_failed_result(
    *,
    investigation_id: str,
    name: str,
    request: InvestigationRequest,
    errors: list[str],
) -> InvestigationResult:
    return InvestigationResult(
        id=investigation_id,
        name=name,
        family="investigation",
        status="failed",
        description=request.description,
        question=request.description,
        hypotheses=[],
        evidence=[],
        conclusion=InvestigationConclusion(best_explanation="", confidence=0.0, limitations=errors),
        unknowns=[],
        recommended_next_steps=[],
        steps_executed=0,
        artifacts=InvestigationArtifacts(investigation_dir=""),
        error="; ".join(errors),
    )


class InvestigationEngine:
    def __init__(
        self,
        *,
        spec_llm_fn: LlmFn,
        knowledge_root: Path,
        analysis_llm_fn: LlmFn | None = None,
    ) -> None:
        self._spec_llm_fn = spec_llm_fn
        self._analysis_llm_fn = analysis_llm_fn or spec_llm_fn
        self._knowledge_root = knowledge_root

    def run(self, request: InvestigationRequest) -> InvestigationResult:
        investigation_id = generate_investigation_id()
        name = request.save_as or derive_investigation_name(request.description)

        try:
            spec_system, spec_user = _build_investigation_spec_prompt(request.description)
            raw_spec = parse_investigation_json(self._spec_llm_fn(spec_system, spec_user))
            if raw_spec is None:
                raise ValueError("Investigation spec generation did not return valid JSON")

            spec = _spec_from_dict(raw_spec)
            source = generate_investigation_class(spec, name)
            source_errors = validate_source_for_family("investigation", source)
            if source_errors:
                raise ValueError("; ".join(source_errors))

            investigation_dir = _persist_investigation_artifacts(
                self._knowledge_root,
                name,
                raw_spec,
                source,
            )
            execution = _execute_generated_investigation(
                source=source,
                name=name,
                max_steps=request.max_steps,
            )

            hypothesis_system, hypothesis_user = _build_hypothesis_prompt(
                description=request.description,
                execution=execution,
                max_hypotheses=request.max_hypotheses,
            )
            question, raw_hypotheses = _parse_hypotheses(
                text=self._analysis_llm_fn(hypothesis_system, hypothesis_user),
                description=request.description,
                max_hypotheses=request.max_hypotheses,
            )

            evidence = _build_evidence(execution)
            hypotheses, annotated_evidence = _evaluate_hypotheses(
                hypotheses=raw_hypotheses,
                evidence=evidence,
                diagnosis_target=spec.diagnosis_target,
            )
            conclusion = _build_conclusion(hypotheses, annotated_evidence)
            unknowns = _identify_unknowns(hypotheses, annotated_evidence)
            next_steps = _recommend_next_steps(hypotheses, unknowns)
            report_path = investigation_dir / "report.json"

            result = InvestigationResult(
                id=investigation_id,
                name=name,
                family="investigation",
                status="completed",
                description=request.description,
                question=question,
                hypotheses=hypotheses,
                evidence=annotated_evidence,
                conclusion=conclusion,
                unknowns=unknowns,
                recommended_next_steps=next_steps,
                steps_executed=execution.steps_executed,
                artifacts=InvestigationArtifacts(
                    investigation_dir=str(investigation_dir),
                    report_path=str(report_path),
                ),
            )
            write_json(report_path, result.to_dict())
            return result
        except Exception as exc:
            return _build_failed_result(
                investigation_id=investigation_id,
                name=name,
                request=request,
                errors=[str(exc)],
            )
