from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Literal, cast

RUBRIC_SPEC_SCHEMA_VERSION = 1
_FINDING_SEVERITY = Literal["warning", "error"]
_SCALE_KIND = Literal["numeric", "binary"]
_GENERIC_WORDS = {"good", "nice", "appropriate", "adequate", "proper"}
_HYPOTHESIS_LOADED = ("prove that", "confirm that", "must show", "obviously")


@dataclass(frozen=True, slots=True)
class RubricScope:
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    @classmethod
    def model_validate(cls, data: dict[str, Any] | None) -> RubricScope | None:
        if not data:
            return None
        return cls(include=_string_list(data.get("include")), exclude=_string_list(data.get("exclude")))

    def has_boundary(self) -> bool:
        return bool(self.include or self.exclude)


@dataclass(frozen=True, slots=True)
class CorpusProfile:
    domain: str = ""
    audience: str = ""
    source_summary: str = ""

    @classmethod
    def model_validate(cls, data: dict[str, Any] | None) -> CorpusProfile | None:
        if not data:
            return None
        return cls(
            domain=str(data.get("domain", "")),
            audience=str(data.get("audience", "")),
            source_summary=str(data.get("source_summary", "")),
        )


@dataclass(frozen=True, slots=True)
class RubricScale:
    id: str
    kind: _SCALE_KIND = "numeric"
    min_score: float = 0.0
    max_score: float = 1.0
    pass_score: float | None = None
    anchors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> RubricScale:
        kind = data.get("kind")
        if kind not in ("numeric", "binary"):
            raise ValueError("scale kind must be 'numeric' or 'binary'")
        return cls(
            id=str(data["id"]),
            kind=cast(_SCALE_KIND, kind),
            min_score=float(data.get("min_score", 0.0)),
            max_score=float(data.get("max_score", 1.0)),
            pass_score=_optional_float(data.get("pass_score")),
            anchors={str(key): str(value) for key, value in dict(data.get("anchors", {})).items()},
        )


@dataclass(frozen=True, slots=True)
class RubricCriterion:
    id: str
    description: str
    scale_id: str
    weight: float = 1.0
    scope: RubricScope | None = None
    evidence_requirements: list[str] = field(default_factory=list)

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> RubricCriterion:
        if "scale_id" not in data:
            raise ValueError("criterion scale_id is required")
        return cls(
            id=str(data["id"]),
            description=str(data["description"]),
            scale_id=str(data["scale_id"]),
            weight=float(data.get("weight", 1.0)),
            scope=RubricScope.model_validate(_optional_dict(data.get("scope"))),
            evidence_requirements=_string_list(data.get("evidence_requirements")),
        )


@dataclass(frozen=True, slots=True)
class RubricDisqualifier:
    id: str
    description: str

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> RubricDisqualifier:
        return cls(id=str(data["id"]), description=str(data["description"]))


@dataclass(frozen=True, slots=True)
class DecisionThresholds:
    pass_score: float = 0.8
    excellent_score: float = 0.9

    @classmethod
    def model_validate(cls, data: dict[str, Any] | None) -> DecisionThresholds | None:
        if not data:
            return None
        return cls(pass_score=float(data.get("pass_score", 0.8)), excellent_score=float(data.get("excellent_score", 0.9)))


@dataclass(frozen=True, slots=True)
class RubricSpec:
    rubric_id: str
    goal: str
    criteria: list[RubricCriterion]
    scales: list[RubricScale]
    schema_version: int = RUBRIC_SPEC_SCHEMA_VERSION
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    scope: RubricScope | None = None
    corpus_profile: CorpusProfile | None = None
    disqualifiers: list[RubricDisqualifier] = field(default_factory=list)
    evidence_requirements: list[str] = field(default_factory=list)
    output_constraints: list[str] = field(default_factory=list)
    decision_thresholds: DecisionThresholds | None = None

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> RubricSpec:
        return cls(
            schema_version=int(data.get("schema_version", RUBRIC_SPEC_SCHEMA_VERSION)),
            rubric_id=str(data["rubric_id"]),
            title=str(data.get("title", "")),
            goal=str(data["goal"]),
            metadata=dict(data.get("metadata", {})),
            scope=RubricScope.model_validate(_optional_dict(data.get("scope"))),
            corpus_profile=CorpusProfile.model_validate(_optional_dict(data.get("corpus_profile"))),
            criteria=[RubricCriterion.model_validate(_require_dict(item)) for item in data.get("criteria", [])],
            scales=[RubricScale.model_validate(_require_dict(item)) for item in data.get("scales", [])],
            disqualifiers=[
                RubricDisqualifier.model_validate(_require_dict(item)) for item in data.get("disqualifiers", [])
            ],
            evidence_requirements=_string_list(data.get("evidence_requirements")),
            output_constraints=_string_list(data.get("output_constraints")),
            decision_thresholds=DecisionThresholds.model_validate(_optional_dict(data.get("decision_thresholds"))),
        )


@dataclass(frozen=True, slots=True)
class RubricFinding:
    code: str
    severity: _FINDING_SEVERITY
    message: str
    path: str = ""


@dataclass(frozen=True, slots=True)
class CompiledRubric:
    schema_version: int
    rubric_id: str
    criterion_ids: list[str]
    result_dimension_ids: list[str]
    scale_ids: list[str]
    normalized_weights: dict[str, float]
    findings: list[RubricFinding]
    prompt_contract: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "rubric_id": self.rubric_id,
            "criterion_ids": list(self.criterion_ids),
            "result_dimension_ids": list(self.result_dimension_ids),
            "scale_ids": list(self.scale_ids),
            "normalized_weights": dict(self.normalized_weights),
            "finding_codes": sorted(finding.code for finding in self.findings),
        }


@dataclass(frozen=True, slots=True)
class RubricPatch:
    op: Literal["append", "replace"]
    path: str
    value: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RubricPatchProposal:
    patches: list[RubricPatch]
    requires_human_review: bool
    metrics: dict[str, float]


def legacy_rubric_spec(rubric: str) -> RubricSpec:
    text = rubric.strip()
    return RubricSpec(
        rubric_id="legacy-string-rubric",
        goal=text,
        criteria=[RubricCriterion(id="overall", description=text, scale_id="score", weight=1.0)],
        scales=[RubricScale(id="score", kind="numeric", min_score=0.0, max_score=1.0)],
    )


def lint_rubric_spec(spec: RubricSpec) -> list[RubricFinding]:
    findings: dict[str, RubricFinding] = {}

    def add(code: str, severity: _FINDING_SEVERITY, message: str, path: str = "") -> None:
        findings.setdefault(code, RubricFinding(code=code, severity=severity, message=message, path=path))

    if spec.schema_version != RUBRIC_SPEC_SCHEMA_VERSION:
        add("unsupported_schema_version", "error", "RubricSpec schema_version must be 1", "/schema_version")
    if not spec.criteria:
        add("missing_criteria", "error", "RubricSpec must declare at least one criterion", "/criteria")
    if not spec.scales:
        add("missing_scales", "error", "RubricSpec must declare at least one scale", "/scales")

    criterion_counts = Counter(criterion.id for criterion in spec.criteria)
    for criterion_id, count in criterion_counts.items():
        if count > 1:
            add("duplicate_criterion_id", "error", f"Duplicate criterion id: {criterion_id}", f"/criteria/{criterion_id}")

    scale_counts = Counter(scale.id for scale in spec.scales)
    for scale_id, count in scale_counts.items():
        if count > 1:
            add("duplicate_scale_id", "error", f"Duplicate scale id: {scale_id}", f"/scales/{scale_id}")

    scale_ids = set(scale_counts)
    for index, scale in enumerate(spec.scales):
        if scale.max_score <= scale.min_score:
            add("invalid_scale_range", "error", "Scale max_score must be greater than min_score", f"/scales/{index}")
        if scale.kind == "binary" and (scale.min_score != 0 or scale.max_score != 1):
            add("invalid_binary_scale", "error", "Binary scales must normalize to 0..1", f"/scales/{index}")

    has_scope = bool((spec.scope and spec.scope.has_boundary()) or spec.corpus_profile)
    for index, criterion in enumerate(spec.criteria):
        if criterion.scale_id not in scale_ids:
            add("unknown_scale", "error", f"Criterion {criterion.id} references unknown scale", f"/criteria/{index}/scale_id")
        if criterion.weight <= 0:
            add("invalid_weight", "error", "Criterion weight must be positive", f"/criteria/{index}/weight")
        if criterion.scope and criterion.scope.has_boundary():
            has_scope = True
        generic_words = [word for word in re.split(r"\W+", criterion.description.lower()) if word in _GENERIC_WORDS]
        if len(generic_words) > 2:
            add(
                "vague_criterion",
                "warning",
                "Criterion uses repeated generic terms",
                f"/criteria/{index}/description",
            )

    if not has_scope:
        add("missing_scope_boundaries", "warning", "Rubric has no explicit scope boundaries", "/scope")

    if any(phrase in spec.goal.lower() for phrase in _HYPOTHESIS_LOADED):
        add("hypothesis_loaded_goal", "warning", "Goal appears to load the desired conclusion", "/goal")

    for index, constraint in enumerate(spec.output_constraints):
        if "xml" in constraint.lower():
            add(
                "unsupported_output_constraint",
                "warning",
                "XML-only output is not supported by the judge parser",
                f"/output_constraints/{index}",
            )

    disqualifier_counts = Counter(disqualifier.id for disqualifier in spec.disqualifiers)
    for disqualifier_id, count in disqualifier_counts.items():
        if count > 1:
            add("duplicate_disqualifier_id", "error", f"Duplicate disqualifier id: {disqualifier_id}")

    return sorted(findings.values(), key=lambda finding: finding.code)


def compile_rubric_spec(spec: RubricSpec | str | dict[str, Any]) -> CompiledRubric:
    rubric_spec = _coerce_spec(spec)
    findings = lint_rubric_spec(rubric_spec)
    errors = [finding.code for finding in findings if finding.severity == "error"]
    if errors:
        raise ValueError(f"invalid rubric: {', '.join(sorted(errors))}")

    total_weight = sum(criterion.weight for criterion in rubric_spec.criteria) or 1.0
    normalized_weights = {
        criterion.id: round(criterion.weight / total_weight, 6) for criterion in rubric_spec.criteria
    }
    criterion_ids = [criterion.id for criterion in rubric_spec.criteria]
    scale_ids = list(dict.fromkeys(scale.id for scale in rubric_spec.scales))
    prompt_contract = render_rubric_prompt(rubric_spec)
    return CompiledRubric(
        schema_version=RUBRIC_SPEC_SCHEMA_VERSION,
        rubric_id=rubric_spec.rubric_id,
        criterion_ids=criterion_ids,
        result_dimension_ids=criterion_ids,
        scale_ids=scale_ids,
        normalized_weights=normalized_weights,
        findings=findings,
        prompt_contract=prompt_contract,
    )


def render_rubric_prompt(spec: RubricSpec | CompiledRubric | str | dict[str, Any]) -> str:
    if isinstance(spec, CompiledRubric):
        return spec.prompt_contract
    rubric_spec = _coerce_spec(spec)
    lines = [f"RubricSpec {rubric_spec.rubric_id}", f"Goal: {rubric_spec.goal}", "Criteria:"]
    for criterion in rubric_spec.criteria:
        lines.append(
            f"- {criterion.id} (weight {criterion.weight:g}, scale {criterion.scale_id}): {criterion.description}"
        )
    if rubric_spec.scope and rubric_spec.scope.has_boundary():
        lines.append(f"Scope include: {', '.join(rubric_spec.scope.include) or 'n/a'}")
        lines.append(f"Scope exclude: {', '.join(rubric_spec.scope.exclude) or 'n/a'}")
    if rubric_spec.corpus_profile:
        lines.append(f"Corpus domain: {rubric_spec.corpus_profile.domain}")
        lines.append(f"Corpus source: {rubric_spec.corpus_profile.source_summary}")
    if rubric_spec.disqualifiers:
        lines.append("Disqualifiers:")
        for disqualifier in rubric_spec.disqualifiers:
            lines.append(f"- {disqualifier.id}: {disqualifier.description}")
    if rubric_spec.evidence_requirements:
        lines.append("Evidence requirements: " + "; ".join(rubric_spec.evidence_requirements))
    if rubric_spec.output_constraints:
        lines.append("Output constraints: " + "; ".join(rubric_spec.output_constraints))
    lines.append("Result dimensions: " + ", ".join(criterion.id for criterion in rubric_spec.criteria))
    return "\n".join(lines)


def propose_rubric_patches(
    spec: RubricSpec,
    anchors: list[dict[str, Any]],
    *,
    experimental: bool = False,
) -> RubricPatchProposal:
    if not experimental:
        raise ValueError("rubric patch proposals are experimental")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    human_scores: list[float] = []
    judge_scores: list[float] = []
    for anchor in anchors:
        criterion_id = str(anchor.get("criterion_id", ""))
        if criterion_id:
            grouped[criterion_id].append(anchor)
        if anchor.get("human_score") is not None and anchor.get("judge_score") is not None:
            human_scores.append(float(anchor["human_score"]))
            judge_scores.append(float(anchor["judge_score"]))

    patches: list[RubricPatch] = []
    known_ids = {criterion.id for criterion in spec.criteria}
    for criterion_id, items in grouped.items():
        if criterion_id not in known_ids or len(items) < 2:
            continue
        mean_gap = mean(
            abs(float(item.get("judge_score", 0.0)) - float(item.get("human_score", 0.0))) for item in items
        )
        if mean_gap < 0.2:
            continue
        note = _first_note(items)
        patches.append(
            RubricPatch(
                op="append",
                path=f"/criteria/{criterion_id}/description",
                value=f" Calibration note: {note}",
                reason=f"human anchors disagree with judge by {mean_gap:.3f}",
            )
        )

    mean_error = mean(abs(j - h) for h, j in zip(human_scores, judge_scores, strict=True)) if human_scores else 0.0
    metrics = {
        "agreement": round(1.0 - mean_error, 6),
        "consistency": round(1.0 - _score_range(judge_scores), 6),
        "discrimination": round(_score_range(human_scores), 6),
    }
    return RubricPatchProposal(patches=patches, requires_human_review=True, metrics=metrics)


def _coerce_spec(spec: RubricSpec | str | dict[str, Any]) -> RubricSpec:
    if isinstance(spec, RubricSpec):
        return spec
    if isinstance(spec, str):
        return legacy_rubric_spec(spec)
    return RubricSpec.model_validate(spec)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _require_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"expected object, got {type(value).__name__}")
    return value


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _score_range(scores: list[float]) -> float:
    return max(scores) - min(scores) if scores else 0.0


def _first_note(items: list[dict[str, Any]]) -> str:
    for item in items:
        note = str(item.get("human_notes", "")).strip()
        if note:
            return note
    return "tighten this criterion against human anchors"
