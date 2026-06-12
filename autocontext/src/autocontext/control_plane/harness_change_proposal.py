from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

_SCHEMA_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_SUITE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_DATE_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


class InvalidHarnessChangeProposalError(Exception):
    """Raised when persisted proposal JSON does not match the shared contract."""


def validate_harness_change_proposal(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a HarnessChangeProposal JSON object before trusting gate decisions."""
    proposal = _expect_mapping(payload, "HarnessChangeProposal")
    _require_keys(
        proposal,
        {
            "schemaVersion",
            "id",
            "status",
            "findingIds",
            "targetSurface",
            "proposedEdit",
            "expectedImpact",
            "rollbackCriteria",
            "provenance",
        },
        "HarnessChangeProposal",
    )
    _reject_extra(
        proposal,
        {
            "schemaVersion",
            "id",
            "status",
            "findingIds",
            "targetSurface",
            "proposedEdit",
            "expectedImpact",
            "rollbackCriteria",
            "provenance",
            "decision",
        },
        "HarnessChangeProposal",
    )
    _expect_pattern(proposal["schemaVersion"], _SCHEMA_VERSION_RE, "schemaVersion")
    _expect_pattern(proposal["id"], _ULID_RE, "id")
    status = _expect_literal(proposal["status"], {"proposed", "accepted", "rejected", "inconclusive"}, "status")
    _expect_non_empty_str_list(proposal["findingIds"], "findingIds")
    _expect_literal(
        proposal["targetSurface"],
        {
            "prompt",
            "tool-schema",
            "tool-affordance-policy",
            "compaction-policy",
            "verifier-rubric",
            "retry-policy",
            "playbook",
        },
        "targetSurface",
    )
    _validate_proposed_edit(proposal["proposedEdit"])
    _validate_expected_impact(proposal["expectedImpact"])
    _expect_non_empty_str_list(proposal["rollbackCriteria"], "rollbackCriteria")
    _validate_provenance(proposal["provenance"])

    if status == "proposed":
        if "decision" in proposal:
            _fail("proposed HarnessChangeProposal must not include decision")
    else:
        if "decision" not in proposal:
            _fail(f"{status} HarnessChangeProposal requires decision")
        _validate_decision(proposal["decision"], status)
    return dict(proposal)


def _validate_proposed_edit(value: Any) -> None:
    edit = _expect_mapping(value, "proposedEdit")
    _require_keys(edit, {"summary", "patches"}, "proposedEdit")
    _reject_extra(edit, {"summary", "patches"}, "proposedEdit")
    _expect_non_empty_str(edit["summary"], "proposedEdit.summary")
    patches = _expect_non_empty_sequence(edit["patches"], "proposedEdit.patches")
    for index, patch in enumerate(patches):
        patch_map = _expect_mapping(patch, f"proposedEdit.patches[{index}]")
        _require_keys(patch_map, {"filePath", "operation", "unifiedDiff"}, f"proposedEdit.patches[{index}]")
        _reject_extra(
            patch_map,
            {"filePath", "operation", "unifiedDiff", "afterContent"},
            f"proposedEdit.patches[{index}]",
        )
        _expect_non_empty_str(patch_map["filePath"], f"proposedEdit.patches[{index}].filePath")
        _expect_literal(patch_map["operation"], {"create", "modify", "delete"}, f"proposedEdit.patches[{index}].operation")
        _expect_str(patch_map["unifiedDiff"], f"proposedEdit.patches[{index}].unifiedDiff")
        if "afterContent" in patch_map:
            _expect_str(patch_map["afterContent"], f"proposedEdit.patches[{index}].afterContent")


def _validate_expected_impact(value: Any) -> None:
    impact = _expect_mapping(value, "expectedImpact")
    _reject_extra(
        impact,
        {"qualityDelta", "costDelta", "latencyDelta", "riskReduction", "notes"},
        "expectedImpact",
    )
    if "qualityDelta" in impact:
        _expect_number(impact["qualityDelta"], "expectedImpact.qualityDelta")
    if "costDelta" in impact:
        _validate_cost_metric(impact["costDelta"], "expectedImpact.costDelta")
    if "latencyDelta" in impact:
        _validate_latency_metric(impact["latencyDelta"], "expectedImpact.latencyDelta")
    if "riskReduction" in impact:
        _expect_non_empty_str(impact["riskReduction"], "expectedImpact.riskReduction")
    if "notes" in impact:
        _expect_str_list(impact["notes"], "expectedImpact.notes")


def _validate_provenance(value: Any) -> None:
    provenance = _expect_mapping(value, "provenance")
    _require_keys(provenance, {"authorType", "authorId", "parentArtifactIds", "createdAt"}, "provenance")
    _reject_extra(provenance, {"authorType", "authorId", "agentRole", "parentArtifactIds", "createdAt"}, "provenance")
    _expect_literal(provenance["authorType"], {"autocontext-run", "human", "external-agent"}, "provenance.authorType")
    _expect_non_empty_str(provenance["authorId"], "provenance.authorId")
    if "agentRole" in provenance:
        _expect_str(provenance["agentRole"], "provenance.agentRole")
    for index, artifact_id in enumerate(_expect_sequence(provenance["parentArtifactIds"], "provenance.parentArtifactIds")):
        _expect_pattern(artifact_id, _ULID_RE, f"provenance.parentArtifactIds[{index}]")
    _expect_date_time(provenance["createdAt"], "provenance.createdAt")


def _validate_decision(value: Any, proposal_status: str) -> None:
    decision = _expect_mapping(value, "decision")
    _require_keys(
        decision,
        {"status", "reason", "validation", "promotionDecision", "candidateArtifactId", "candidateEvalRunId", "decidedAt"},
        "decision",
    )
    _reject_extra(
        decision,
        {
            "status",
            "reason",
            "validation",
            "promotionDecision",
            "candidateArtifactId",
            "candidateEvalRunId",
            "baselineArtifactId",
            "baselineEvalRunId",
            "decidedAt",
        },
        "decision",
    )
    decision_status = _expect_literal(decision["status"], {"accepted", "rejected", "inconclusive"}, "decision.status")
    if decision_status != proposal_status:
        _fail("HarnessChangeProposal decision.status must match status")
    _expect_non_empty_str(decision["reason"], "decision.reason")
    validation = _validate_validation(decision["validation"])
    _validate_promotion_decision(decision["promotionDecision"])
    _expect_pattern(decision["candidateArtifactId"], _ULID_RE, "decision.candidateArtifactId")
    _expect_non_empty_str(decision["candidateEvalRunId"], "decision.candidateEvalRunId")
    if "baselineArtifactId" in decision:
        _expect_pattern(decision["baselineArtifactId"], _ULID_RE, "decision.baselineArtifactId")
    if "baselineEvalRunId" in decision:
        _expect_non_empty_str(decision["baselineEvalRunId"], "decision.baselineEvalRunId")
    _expect_date_time(decision["decidedAt"], "decision.decidedAt")
    if proposal_status in {"accepted", "rejected"}:
        if validation["mode"] not in {"heldout", "fresh"}:
            _fail("accepted/rejected HarnessChangeProposal requires heldout or fresh validation")
        if not validation["evidenceRefs"]:
            _fail("accepted/rejected HarnessChangeProposal requires validation evidenceRefs")
        if "baselineArtifactId" not in decision or "baselineEvalRunId" not in decision:
            _fail("accepted/rejected HarnessChangeProposal requires baseline artifact and eval refs")


def _validate_validation(value: Any) -> Mapping[str, Any]:
    validation = _expect_mapping(value, "decision.validation")
    _require_keys(validation, {"mode", "suiteId", "evidenceRefs"}, "decision.validation")
    _reject_extra(validation, {"mode", "suiteId", "evidenceRefs"}, "decision.validation")
    _expect_literal(validation["mode"], {"dev", "heldout", "fresh"}, "decision.validation.mode")
    _expect_pattern(validation["suiteId"], _SUITE_ID_RE, "decision.validation.suiteId")
    _expect_str_list(validation["evidenceRefs"], "decision.validation.evidenceRefs", require_non_empty_items=True)
    return validation


def _validate_promotion_decision(value: Any) -> None:
    decision = _expect_mapping(value, "decision.promotionDecision")
    _require_keys(
        decision,
        {"schemaVersion", "pass", "recommendedTargetState", "deltas", "confidence", "thresholds", "reasoning", "evaluatedAt"},
        "decision.promotionDecision",
    )
    _reject_extra(
        decision,
        {
            "schemaVersion",
            "pass",
            "recommendedTargetState",
            "deltas",
            "confidence",
            "thresholds",
            "ablationVerification",
            "reasoning",
            "evaluatedAt",
        },
        "decision.promotionDecision",
    )
    _expect_pattern(decision["schemaVersion"], _SCHEMA_VERSION_RE, "decision.promotionDecision.schemaVersion")
    _expect_bool(decision["pass"], "decision.promotionDecision.pass")
    _expect_literal(
        decision["recommendedTargetState"],
        {"shadow", "canary", "active", "disabled"},
        "decision.promotionDecision.recommendedTargetState",
    )
    _validate_promotion_deltas(decision["deltas"])
    confidence = _expect_number(decision["confidence"], "decision.promotionDecision.confidence")
    if not 0 <= confidence <= 1:
        _fail("decision.promotionDecision.confidence must be between 0 and 1")
    _validate_thresholds(decision["thresholds"])
    if "ablationVerification" in decision:
        _validate_ablation_verification(decision["ablationVerification"])
    _expect_str(decision["reasoning"], "decision.promotionDecision.reasoning")
    _expect_date_time(decision["evaluatedAt"], "decision.promotionDecision.evaluatedAt")


def _validate_promotion_deltas(value: Any) -> None:
    deltas = _expect_mapping(value, "decision.promotionDecision.deltas")
    _require_keys(deltas, {"quality", "cost", "latency", "safety"}, "decision.promotionDecision.deltas")
    _reject_extra(deltas, {"quality", "cost", "latency", "safety", "humanFeedback"}, "decision.promotionDecision.deltas")
    _validate_quality_delta(deltas["quality"])
    _validate_metric_delta(deltas["cost"], _validate_cost_metric, "decision.promotionDecision.deltas.cost")
    _validate_metric_delta(deltas["latency"], _validate_latency_metric, "decision.promotionDecision.deltas.latency")
    _validate_safety_delta(deltas["safety"])
    if "humanFeedback" in deltas:
        feedback = _expect_mapping(deltas["humanFeedback"], "decision.promotionDecision.deltas.humanFeedback")
        _require_keys(feedback, {"delta", "passed"}, "decision.promotionDecision.deltas.humanFeedback")
        _reject_extra(feedback, {"delta", "passed"}, "decision.promotionDecision.deltas.humanFeedback")
        _expect_number(feedback["delta"], "decision.promotionDecision.deltas.humanFeedback.delta")
        _expect_bool(feedback["passed"], "decision.promotionDecision.deltas.humanFeedback.passed")


def _validate_quality_delta(value: Any) -> None:
    quality = _expect_mapping(value, "decision.promotionDecision.deltas.quality")
    _require_keys(quality, {"baseline", "candidate", "delta", "passed"}, "decision.promotionDecision.deltas.quality")
    _reject_extra(quality, {"baseline", "candidate", "delta", "passed"}, "decision.promotionDecision.deltas.quality")
    _expect_number(quality["baseline"], "decision.promotionDecision.deltas.quality.baseline")
    _expect_number(quality["candidate"], "decision.promotionDecision.deltas.quality.candidate")
    _expect_number(quality["delta"], "decision.promotionDecision.deltas.quality.delta")
    _expect_bool(quality["passed"], "decision.promotionDecision.deltas.quality.passed")


def _validate_metric_delta(value: Any, metric_validator: Any, path: str) -> None:
    delta = _expect_mapping(value, path)
    _require_keys(delta, {"baseline", "candidate", "delta", "passed"}, path)
    _reject_extra(delta, {"baseline", "candidate", "delta", "passed"}, path)
    metric_validator(delta["baseline"], f"{path}.baseline")
    metric_validator(delta["candidate"], f"{path}.candidate")
    metric_validator(delta["delta"], f"{path}.delta")
    _expect_bool(delta["passed"], f"{path}.passed")


def _validate_safety_delta(value: Any) -> None:
    safety = _expect_mapping(value, "decision.promotionDecision.deltas.safety")
    _require_keys(safety, {"regressions", "passed"}, "decision.promotionDecision.deltas.safety")
    _reject_extra(safety, {"regressions", "passed"}, "decision.promotionDecision.deltas.safety")
    regression_path = "decision.promotionDecision.deltas.safety.regressions"
    for index, regression in enumerate(_expect_sequence(safety["regressions"], regression_path)):
        item_path = f"{regression_path}[{index}]"
        regression_map = _expect_mapping(regression, item_path)
        _require_keys(regression_map, {"id", "severity", "description"}, item_path)
        _reject_extra(
            regression_map,
            {"id", "severity", "description", "exampleRef"},
            item_path,
        )
        _expect_non_empty_str(regression_map["id"], f"{item_path}.id")
        _expect_literal(
            regression_map["severity"],
            {"info", "minor", "major", "critical"},
            f"{item_path}.severity",
        )
        _expect_str(regression_map["description"], f"{item_path}.description")
        if "exampleRef" in regression_map:
            _expect_str(regression_map["exampleRef"], f"{item_path}.exampleRef")
    _expect_bool(safety["passed"], "decision.promotionDecision.deltas.safety.passed")


def _validate_thresholds(value: Any) -> None:
    thresholds = _expect_mapping(value, "decision.promotionDecision.thresholds")
    _require_keys(
        thresholds,
        {
            "qualityMinDelta",
            "costMaxRelativeIncrease",
            "latencyMaxRelativeIncrease",
            "strongConfidenceMin",
            "moderateConfidenceMin",
            "strongQualityMultiplier",
        },
        "decision.promotionDecision.thresholds",
    )
    _reject_extra(
        thresholds,
        {
            "qualityMinDelta",
            "costMaxRelativeIncrease",
            "latencyMaxRelativeIncrease",
            "humanFeedbackMinDelta",
            "strongConfidenceMin",
            "moderateConfidenceMin",
            "strongQualityMultiplier",
        },
        "decision.promotionDecision.thresholds",
    )
    for field in thresholds:
        _expect_number(thresholds[field], f"decision.promotionDecision.thresholds.{field}")


def _validate_ablation_verification(value: Any) -> None:
    ablation = _expect_mapping(value, "decision.promotionDecision.ablationVerification")
    _require_keys(
        ablation,
        {"required", "status", "requiredTargets", "coveredTargets", "missingTargets"},
        "decision.promotionDecision.ablationVerification",
    )
    _reject_extra(
        ablation,
        {"required", "status", "requiredTargets", "coveredTargets", "missingTargets", "reason"},
        "decision.promotionDecision.ablationVerification",
    )
    _expect_bool(ablation["required"], "decision.promotionDecision.ablationVerification.required")
    _expect_literal(
        ablation["status"],
        {"not-required", "missing", "incomplete", "failed", "passed"},
        "decision.promotionDecision.ablationVerification.status",
    )
    ablation_path = "decision.promotionDecision.ablationVerification"
    for field in ("requiredTargets", "coveredTargets", "missingTargets"):
        target_path = f"{ablation_path}.{field}"
        for index, target in enumerate(_expect_sequence(ablation[field], target_path)):
            _expect_literal(
                target,
                {"strategy", "harness"},
                f"{target_path}[{index}]",
            )
    if "reason" in ablation:
        _expect_non_empty_str(ablation["reason"], "decision.promotionDecision.ablationVerification.reason")


def _validate_cost_metric(value: Any, path: str) -> None:
    metric = _expect_mapping(value, path)
    _require_keys(metric, {"tokensIn", "tokensOut"}, path)
    _reject_extra(metric, {"tokensIn", "tokensOut", "usd"}, path)
    _expect_number(metric["tokensIn"], f"{path}.tokensIn")
    _expect_number(metric["tokensOut"], f"{path}.tokensOut")
    if "usd" in metric:
        _expect_number(metric["usd"], f"{path}.usd")


def _validate_latency_metric(value: Any, path: str) -> None:
    metric = _expect_mapping(value, path)
    _require_keys(metric, {"p50Ms", "p95Ms", "p99Ms"}, path)
    _reject_extra(metric, {"p50Ms", "p95Ms", "p99Ms"}, path)
    _expect_number(metric["p50Ms"], f"{path}.p50Ms")
    _expect_number(metric["p95Ms"], f"{path}.p95Ms")
    _expect_number(metric["p99Ms"], f"{path}.p99Ms")


def _expect_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{path} must be an object")
    return cast(Mapping[str, Any], value)


def _expect_sequence(value: Any, path: str) -> Sequence[Any]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        _fail(f"{path} must be an array")
    return cast(Sequence[Any], value)


def _expect_non_empty_sequence(value: Any, path: str) -> Sequence[Any]:
    sequence = _expect_sequence(value, path)
    if not sequence:
        _fail(f"{path} must contain at least one item")
    return sequence


def _expect_str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        _fail(f"{path} must be a string")
    return cast(str, value)


def _expect_non_empty_str(value: Any, path: str) -> str:
    text = _expect_str(value, path)
    if not text:
        _fail(f"{path} must not be empty")
    return text


def _expect_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{path} must be a boolean")
    return cast(bool, value)


def _expect_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{path} must be a number")
    return float(value)


def _expect_literal(value: Any, allowed: set[str], path: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        _fail(f"{path} must be one of {', '.join(sorted(allowed))}")
    return cast(str, value)


def _expect_pattern(value: Any, pattern: re.Pattern[str], path: str) -> str:
    text = _expect_str(value, path)
    if not pattern.fullmatch(text):
        _fail(f"{path} has invalid format")
    return text


def _expect_date_time(value: Any, path: str) -> str:
    text = _expect_pattern(value, _DATE_TIME_RE, path)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"{path} must be a valid date-time")
    return text


def _expect_str_list(value: Any, path: str, *, require_non_empty_items: bool = True) -> list[str]:
    items = _expect_sequence(value, path)
    strings: list[str] = []
    for index, item in enumerate(items):
        strings.append(
            _expect_non_empty_str(item, f"{path}[{index}]") if require_non_empty_items else _expect_str(item, f"{path}[{index}]")
        )
    return strings


def _expect_non_empty_str_list(value: Any, path: str) -> list[str]:
    strings = _expect_str_list(value, path)
    if not strings:
        _fail(f"{path} must contain at least one item")
    return strings


def _require_keys(mapping: Mapping[str, Any], required: set[str], path: str) -> None:
    missing = sorted(required - set(mapping))
    if missing:
        _fail(f"{path} missing required field(s): {', '.join(missing)}")


def _reject_extra(mapping: Mapping[str, Any], allowed: set[str], path: str) -> None:
    extra = sorted(set(mapping) - allowed)
    if extra:
        _fail(f"{path} has unexpected field(s): {', '.join(extra)}")


def _fail(message: str) -> None:
    raise InvalidHarnessChangeProposalError(message)
