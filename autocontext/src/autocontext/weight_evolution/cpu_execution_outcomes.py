"""Pure operation bindings and replay, with a separate current-profile factory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import TypeAdapter

from autocontext.weight_evolution.artifacts import canonical_digest, file_identity
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateSpec, VerifiedTensorCandidate
from autocontext.weight_evolution.cpu_execution_models import (
    EXECUTION_METERS,
    MAX_OPERATION_OUTCOME_BYTES,
    CpuExecutionContract,
    CpuExecutionScope,
    CpuExecutionUsage,
    CpuOperationClaim,
    CpuOperationOutcome,
    validate_execution_json,
)
from autocontext.weight_evolution.cpu_failure_v2_models import CpuFailureContractV2, CpuFailureRecordV2
from autocontext.weight_evolution.cpu_failure_v2_record import build_cpu_failure_record_v2, validate_cpu_failure_record_v2
from autocontext.weight_evolution.cpu_measured_models import CpuMeasuredBatchOutcome
from autocontext.weight_evolution.cpu_measured_runner import measured_runtime_identity, validate_measured_outcome
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest
from autocontext.weight_evolution.cpu_search_models import CpuSearchOption, CpuSearchPolicy
from autocontext.weight_evolution.cpu_search_policy import CpuPracticeScore, choose_policy_option
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

MAX_OPERATION_CLAIM_BYTES = 512 * 1024
MAX_OPERATION_VALUE_BYTES = 64 * 1024
_Model = TypeVar("_Model", bound=FrozenModel)


def execution_implementation_digest() -> str:
    package = Path(__file__).parent
    return canonical_digest({"implementation": "measured-cpu-workflow-v1", "sources": {
        path.name: file_identity(path, max_bytes=1024 * 1024) for path in sorted(package.glob("cpu_execution_*.py"))}})


def execution_runtime_identity() -> dict[str, Any]:
    """Read current pins once; outcome capture/replay never calls this factory."""
    measured = measured_runtime_identity()
    native = measured["native_runtime"]
    return {"schema_version": 1, "implementation_digest": execution_implementation_digest(),
            "native_runtime_digest": canonical_digest(native), "measured_profile_digest": canonical_digest(measured),
            "native_runtime": native, "measured_profile": measured}


def build_cpu_execution_contract(
    *, scope: CpuExecutionScope, design_digest: str, workflow_digest: str, workflow_implementation_digest: str,
    capacity: CpuExecutionUsage, failure_contract: CpuFailureContractV2 | None = None,
) -> CpuExecutionContract:
    identity = execution_runtime_identity()
    return CpuExecutionContract(scope=scope, design_digest=design_digest, workflow_digest=workflow_digest,
        workflow_implementation_digest=workflow_implementation_digest,
        implementation_digest=identity["implementation_digest"], native_runtime_digest=identity["native_runtime_digest"],
        measured_profile_digest=identity["measured_profile_digest"],
        failure_contract=failure_contract or CpuFailureContractV2(), capacity=capacity)


def _contract(contract: CpuExecutionContract) -> CpuExecutionContract:
    return CpuExecutionContract.model_validate_json(json.dumps(contract.to_dict(), allow_nan=False))


def _same(first: Any, second: Any, message: str) -> None:
    if canonical_digest(first) != canonical_digest(second):
        raise ValueError(message)


def _typed(model: type[_Model], raw: Any) -> _Model:
    parsed = model.model_validate_json(json.dumps(raw, allow_nan=False))
    _same(raw, parsed.to_dict(), "operation input changed its exact typed representation")
    return parsed


def _proposal(inputs: dict[str, Any]) -> tuple[CpuSearchPolicy, tuple[CpuSearchOption, ...], tuple[CpuPracticeScore, ...]]:
    if set(inputs) != {"mechanism", "policy", "policy_digest", "options", "history", "practice_receipt_digests"}:
        raise ValueError("proposal must contain only the pinned practice-policy inputs")
    if (type(inputs["options"]) is not list or not 1 <= len(inputs["options"]) <= 16
            or type(inputs["history"]) is not list or not 1 <= len(inputs["history"]) <= 17
            or type(inputs["practice_receipt_digests"]) is not list
            or len(inputs["practice_receipt_digests"]) != len(inputs["history"])):
        raise ValueError("proposal practice evidence exceeds its exact inventory bound")
    policy = _typed(CpuSearchPolicy, inputs["policy"])
    options = tuple(_typed(CpuSearchOption, item) for item in inputs["options"])
    history = tuple(_typed(CpuPracticeScore, item) for item in inputs["history"])
    if (inputs["mechanism"] not in {"C", "W", "D"} or policy.digest != inputs["policy_digest"]
            or any(option.mechanism != inputs["mechanism"] for option in options)
            or len({option.option_id for option in options}) != len(options)
            or len({(option.direction, option.step) for option in options}) != len(options)
            or history[0].option_id is not None or any(item.option_id is None for item in history[1:])
            or len({item.option_id for item in history}) != len(history)
            or any(item.total != history[0].total for item in history)
            or any(item.option_id not in {option.option_id for option in options} for item in history[1:])):
        raise ValueError("proposal identity or complete practice history differs")
    for digest in inputs["practice_receipt_digests"]:
        TypeAdapter(Digest).validate_python(digest)
    return policy, options, history


def _claim(contract: CpuExecutionContract, raw: dict[str, Any]) -> tuple[CpuOperationClaim, tuple[CpuInferenceRequest, ...]]:
    validate_execution_json(raw, MAX_OPERATION_CLAIM_BYTES)
    claim = _typed(CpuOperationClaim, raw)
    if claim.contract_digest != contract.digest:
        raise ValueError("operation claim belongs to another execution contract")
    if ((contract.scope == "search" and claim.kind == "checkpoint-verification")
            or (contract.scope == "reference" and claim.kind in {"materialize", "proposal"})):
        raise ValueError("operation kind differs from workflow scope")
    if any(getattr(claim.reserved, meter) > getattr(contract.capacity, meter) for meter in EXECUTION_METERS):
        raise ValueError("operation reservation exceeds the pinned workflow capacity")
    inputs = claim.input
    requests: tuple[CpuInferenceRequest, ...] = ()
    if claim.kind == "inference":
        fields = ({"candidate_digest", "case_ids", "requests"} if contract.scope == "search" else
                  {"candidate_digest", "option_id", "practice_cases_digest", "requests"})
        if (set(inputs) != fields or type(inputs["requests"]) is not list
                or not 1 <= len(inputs["requests"]) <= 32):
            raise ValueError("inference claim differs from the exact bounded native request inventory")
        TypeAdapter(Digest).validate_python(inputs["candidate_digest"])
        requests = tuple(_typed(CpuInferenceRequest, item) for item in inputs["requests"])
        first = requests[0]
        if (len({request.request_id for request in requests}) != len(requests)
                or any((request.model, request.checkpoint_sha256, request.checkpoint_size_bytes, request.limits)
                       != (first.model, first.checkpoint_sha256, first.checkpoint_size_bytes, first.limits)
                       for request in requests)):
            raise ValueError("inference requests must have unique IDs and shared model/checkpoint/limits")
        if contract.scope == "search":
            ids = inputs["case_ids"]
            if (type(ids) is not list or any(type(case_id) is not str for case_id in ids)
                    or len(ids) != len(requests) or len(set(ids)) != len(ids)):
                raise ValueError("inference case IDs differ from complete request coverage")
            for case_id in ids:
                TypeAdapter(Identifier).validate_python(case_id)
        else:
            TypeAdapter(Digest).validate_python(inputs["practice_cases_digest"])
            if inputs["option_id"] != claim.label:
                raise ValueError("reference option identity differs from operation label")
        expected = CpuExecutionUsage(inference_calls=len(requests), output_tokens=len(requests),
                                    input_tokens=sum(len(request.prompt.split()) for request in requests))
    elif claim.kind == "materialize":
        if set(inputs) != {"candidate"}:
            raise ValueError("materialization requires only its exact candidate")
        _typed(TensorCandidateSpec, inputs["candidate"])
        expected = CpuExecutionUsage(materializations=1)
    elif claim.kind == "checkpoint-verification":
        if set(inputs) != {"candidate_digest"}:
            raise ValueError("checkpoint verification requires only its candidate digest")
        TypeAdapter(Digest).validate_python(inputs["candidate_digest"])
        expected = CpuExecutionUsage(materializations=1)
    else:
        _proposal(inputs)
        expected = CpuExecutionUsage(proposals=1)
    if claim.reserved != expected:
        raise ValueError("operation reservation differs from exact kind/request charges")
    return claim, requests


def operation_requests(contract: CpuExecutionContract, claim: dict[str, Any]) -> tuple[CpuInferenceRequest, ...]:
    """Parse independently before dispatch; never accept caller-supplied substitute requests."""
    return _claim(_contract(contract), claim)[1]


def _redact(claim: CpuOperationClaim, requests: tuple[CpuInferenceRequest, ...]) -> tuple[str, ...]:
    if requests:
        return tuple(request.prompt for request in requests)
    if claim.kind == "proposal":
        return tuple(option.context for option in _proposal(claim.input)[1] if option.context is not None)
    return ()


def _failure_bindings(contract: CpuExecutionContract, claim: CpuOperationClaim, *, check: bool = False) -> dict[str, Any]:
    return {"supervisor_digest": contract.digest, "run_id": f"operation-{'check-' if check else ''}{claim.index}",
            "study_digest": claim.digest, "runtime_digest": contract.native_runtime_digest,
            "terminal_claim": None, "terminal_result": None}


def _returned_value(claim: CpuOperationClaim, value: dict[str, Any]) -> None:
    validate_execution_json(value, MAX_OPERATION_VALUE_BYTES)
    if type(value) is not dict:
        raise ValueError("operation result must be an exact JSON object")
    if claim.kind == "proposal":
        policy, options, history = _proposal(claim.input)
        expected = choose_policy_option(policy, options, history)
        _same(value, expected.to_dict(), "proposal result differs from its pinned practice policy")
    else:
        verified = _typed(VerifiedTensorCandidate, value)
        if claim.kind == "materialize":
            candidate = _typed(TensorCandidateSpec, claim.input["candidate"])
            if verified.reference != candidate.reference or verified.contract_digest != candidate.contract.digest:
                raise ValueError("materialization result differs from its declared candidate")
        elif verified.reference.candidate_digest != claim.input["candidate_digest"]:
            raise ValueError("verified checkpoint differs from its declared candidate")


def build_cpu_operation_outcome(
    contract: CpuExecutionContract, claim: dict[str, Any], *, measured: CpuMeasuredBatchOutcome | None = None,
    value: dict[str, Any] | None = None, exception: BaseException | None = None,
) -> CpuOperationOutcome:
    """Build only from a returned boundary; unreturned inference remains a pending claim."""
    contract = _contract(contract)
    parsed, requests = _claim(contract, claim)
    failure = None
    if parsed.kind == "inference":
        if measured is None or value is not None or exception is not None:
            raise ValueError("unreturned measured inference cannot be converted into an operation failure")
        status = measured.status
    else:
        if measured is not None or (value is None) == (exception is None):
            raise ValueError("noninference requires exactly a returned value or observed exception")
        if exception is not None:
            failure = build_cpu_failure_record_v2(contract.failure_contract, **_failure_bindings(contract, parsed),
                                                 exception=exception, redact=_redact(parsed, requests))
        status = "failed" if failure is not None else "completed"
    outcome = CpuOperationOutcome(contract_digest=contract.digest, operation_claim_digest=parsed.digest,
        operation_index=parsed.index, operation_kind=parsed.kind, operation_label=parsed.label,
        operation_input_digest=canonical_digest(parsed.input), reserved=parsed.reserved, status=status,
        measured=measured, value=value, failure=failure)
    return validate_cpu_operation_outcome(outcome, contract, claim)


def validate_cpu_operation_outcome(
    outcome: CpuOperationOutcome | dict[str, Any], contract: CpuExecutionContract, claim: dict[str, Any],
) -> CpuOperationOutcome:
    """Pure replay against saved pins; live filesystem/source checks belong to the caller."""
    contract = _contract(contract)
    parsed, requests = _claim(contract, claim)
    raw = outcome.to_dict() if isinstance(outcome, CpuOperationOutcome) else outcome
    validate_execution_json(raw, MAX_OPERATION_OUTCOME_BYTES)
    outcome = _typed(CpuOperationOutcome, raw)
    expected = {"contract_digest": contract.digest, "operation_claim_digest": parsed.digest,
                "operation_index": parsed.index, "operation_kind": parsed.kind, "operation_label": parsed.label,
                "operation_input_digest": canonical_digest(parsed.input), "reserved": parsed.reserved.to_dict()}
    actual = outcome.to_dict()
    for key, bound in expected.items():
        _same(actual[key], bound, "operation outcome differs from its exact claim/contract binding")
    if outcome.measured is not None:
        validate_measured_outcome(outcome.measured, requests, contract=contract.failure_contract,
                                 profile_digest=contract.measured_profile_digest,
                                 native_runtime_digest=contract.native_runtime_digest)
    elif outcome.failure is not None:
        validate_cpu_failure_record_v2(outcome.failure, contract.failure_contract,
            **_failure_bindings(contract, parsed), redact=_redact(parsed, requests))
    elif outcome.value is not None:
        # Preserve the exact returned boundary before the native callback checks
        # its semantics. Malformed-but-serializable values receive rejected checks.
        validate_execution_json(outcome.value, MAX_OPERATION_VALUE_BYTES)
    return outcome


def operation_value(outcome: CpuOperationOutcome) -> dict[str, Any]:
    """Project unchanged native workflow values only after successful boundary return."""
    outcome = CpuOperationOutcome.model_validate_json(json.dumps(outcome.to_dict(), allow_nan=False))
    if outcome.status != "completed":
        raise ValueError("failed operation has no accepted workflow value")
    if outcome.measured is not None:
        assert outcome.measured.receipt is not None
        return outcome.measured.receipt.to_dict()
    assert outcome.value is not None
    return outcome.value


def build_cpu_operation_check(
    contract: CpuExecutionContract, claim: dict[str, Any], outcome: CpuOperationOutcome,
    *, exception: BaseException | None = None,
) -> dict[str, Any]:
    outcome = validate_cpu_operation_outcome(outcome, contract, claim)
    if outcome.status != "completed":
        raise ValueError("failed returned operation does not receive a success-validation check")
    parsed, requests = _claim(contract, claim)
    failure = (build_cpu_failure_record_v2(contract.failure_contract, **_failure_bindings(contract, parsed, check=True),
        exception=exception, redact=_redact(parsed, requests)) if exception is not None else None)
    result = {"schema_version": 1, "contract_digest": contract.digest, "operation_claim_digest": parsed.digest,
              "outcome_digest": outcome.digest, "status": "rejected" if failure is not None else "accepted",
              "failure": failure.to_dict() if failure is not None else None}
    return validate_cpu_operation_check(result, contract, claim, outcome)


def validate_cpu_operation_check(
    raw: dict[str, Any], contract: CpuExecutionContract, claim: dict[str, Any], outcome: CpuOperationOutcome,
) -> dict[str, Any]:
    outcome = validate_cpu_operation_outcome(outcome, contract, claim)
    parsed, requests = _claim(contract, claim)
    validate_execution_json(raw, MAX_OPERATION_VALUE_BYTES * 3)
    if (set(raw) != {"schema_version", "contract_digest", "operation_claim_digest", "outcome_digest", "status", "failure"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or raw["contract_digest"] != contract.digest or raw["operation_claim_digest"] != parsed.digest
            or raw["outcome_digest"] != outcome.digest or outcome.status != "completed"
            or raw["status"] not in {"accepted", "rejected"}
            or (raw["status"] == "accepted") != (raw["failure"] is None)):
        raise ValueError("operation validation check differs from its exact successful outcome binding")
    if raw["failure"] is not None:
        failure = _typed(CpuFailureRecordV2, raw["failure"])
        validate_cpu_failure_record_v2(failure, contract.failure_contract,
            **_failure_bindings(contract, parsed, check=True), redact=_redact(parsed, requests))
        if (failure.observation_kind != "live-exception"
                or any(value is not None for value in failure.observations.to_dict().values())):
            raise ValueError("callback validation failure requires an observed exception and no invented process telemetry")
    elif outcome.value is not None:
        _returned_value(parsed, outcome.value)
    copied: dict[str, Any] = json.loads(json.dumps(raw, allow_nan=False))
    return copied
