from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_execution_outcomes as module
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateSpec, VerifiedTensorCandidate
from autocontext.weight_evolution.cpu_execution_models import CpuExecutionContract, CpuExecutionUsage
from autocontext.weight_evolution.cpu_execution_outcomes import (
    build_cpu_operation_check,
    build_cpu_operation_outcome,
    operation_requests,
    operation_value,
    validate_cpu_operation_check,
    validate_cpu_operation_outcome,
)
from autocontext.weight_evolution.cpu_failure_v2_record import build_cpu_failure_record_v2, capture_observations_v2
from autocontext.weight_evolution.cpu_measured_models import CpuMeasuredBatchOutcome
from autocontext.weight_evolution.cpu_measured_transport import CpuProcessObservation
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest, CpuInferenceResult, CpuModelContract
from autocontext.weight_evolution.cpu_runner import CpuBatchReceipt, CpuTelemetry, resource_policy
from autocontext.weight_evolution.cpu_search_models import CpuSearchOption, CpuSearchPolicy
from autocontext.weight_evolution.cpu_search_policy import CpuPracticeScore, choose_policy_option
from autocontext.weight_evolution.tensor_edits import CheckpointContract, TensorContract


def contract(scope: str = "search") -> CpuExecutionContract:
    return CpuExecutionContract.model_validate({"scope": scope, "design_digest": "e" * 64,
        "workflow_digest": ("e" if scope == "search" else "9") * 64,
        "workflow_implementation_digest": "8" * 64, "implementation_digest": "f" * 64,
        "native_runtime_digest": "c" * 64, "measured_profile_digest": "b" * 64,
        "capacity": CpuExecutionUsage(proposals=9 if scope == "search" else 0, materializations=5,
                                       inference_calls=208, input_tokens=1664, output_tokens=208)})


def requests() -> tuple[CpuInferenceRequest, ...]:
    model = CpuModelContract(vocabulary=("a", "prompt"), output_tokens=("yes", "no"))
    return tuple(CpuInferenceRequest(request_id=f"probe-{index}", model=model, prompt=prompt,
        checkpoint_sha256="d" * 64, checkpoint_size_bytes=256) for index, prompt in enumerate(("a", "prompt")))


def claim(bound: CpuExecutionContract, kind: str = "inference") -> dict[str, Any]:
    if kind == "inference":
        inputs = {"candidate_digest": "a" * 64, "requests": [request.to_dict() for request in requests()]}
        if bound.scope == "search":
            inputs["case_ids"] = ["case-0", "case-1"]
        else:
            inputs.update(option_id="A", practice_cases_digest="7" * 64)
        reserved = CpuExecutionUsage(inference_calls=2, input_tokens=2, output_tokens=2)
    elif kind == "proposal":
        policy = CpuSearchPolicy()
        options = (CpuSearchOption(option_id="c0", mechanism="C", direction="left", step=1, context="a prompt"),)
        history = (CpuPracticeScore(correct=0, total=2),)
        inputs = {"mechanism": "C", "policy": policy.to_dict(), "policy_digest": policy.digest,
                  "options": [option.to_dict() for option in options], "history": [row.to_dict() for row in history],
                  "practice_receipt_digests": ["6" * 64]}
        reserved = CpuExecutionUsage(proposals=1)
    elif kind == "materialize":
        inputs = {"candidate": candidate().to_dict()}
        reserved = CpuExecutionUsage(materializations=1)
    else:
        inputs = {"candidate_digest": candidate().digest}
        reserved = CpuExecutionUsage(materializations=1)
    return {"schema_version": 1, "contract_digest": bound.digest, "index": 3, "kind": kind,
            "label": "A" if bound.scope == "reference" else "C-baseline", "input": inputs,
            "reserved": reserved.to_dict(), "previous_result_digest": "0" * 64}


def candidate() -> TensorCandidateSpec:
    pinned = CheckpointContract(sha256="d" * 64, size_bytes=256,
        tensors=(TensorContract(name="projection.weight", dtype="F32", shape=(2, 2), sha256="6" * 64),))
    return TensorCandidateSpec(candidate_id="base", contract=pinned, output_sha256="d" * 64, output_size_bytes=256)


def returned(raw: dict[str, Any]) -> dict[str, Any]:
    if raw["kind"] == "proposal":
        inputs = raw["input"]
        return choose_policy_option(CpuSearchPolicy.model_validate_json(json.dumps(inputs["policy"])),
            tuple(CpuSearchOption.model_validate_json(json.dumps(row)) for row in inputs["options"]),
            tuple(CpuPracticeScore.model_validate_json(json.dumps(row)) for row in inputs["history"])).to_dict()
    pinned = candidate()
    return VerifiedTensorCandidate(reference=pinned.reference, contract_digest=pinned.contract.digest,
        verification_digest="7" * 64, tensor_content_digest="6" * 64, changed_entries=0,
        implementation_digest="5" * 64).to_dict()


def measured(bound: CpuExecutionContract, *, failed: bool = False) -> CpuMeasuredBatchOutcome:
    rows = requests()
    batch = canonical_digest({"request_digests": [request.digest for request in rows],
                              "runtime_digest": bound.native_runtime_digest})
    common = {"contract": bound.failure_contract, "profile_digest": bound.measured_profile_digest,
              "native_runtime_digest": bound.native_runtime_digest, "batch_digest": batch,
              "request_digests": tuple(request.digest for request in rows), "checkpoint_sha256": "d" * 64,
              "checkpoint_size_bytes": 256, "model_digest": rows[0].model.digest}
    if failed:
        process = CpuProcessObservation(trigger="wall-deadline", dispatched=True, elapsed_to_trigger_seconds=5.0,
            total_seconds=5.01, cleanup_seconds=0.01, state_before_cleanup="running", returncode_after_cleanup=-9,
            signal_attempted=True, signal_sent=True, signal_number=9, cleanup_state="completed", leader_reaped=True)
        observations = capture_observations_v2(bound.failure_contract, elapsed_seconds=5.0,
            stdout=b"a prompt", stderr=b"", redact=tuple(request.prompt for request in rows))
        failure = build_cpu_failure_record_v2(bound.failure_contract, supervisor_digest=bound.measured_profile_digest,
            study_digest=batch, runtime_digest=bound.native_runtime_digest, run_id="measured-batch",
            terminal_claim=None, terminal_result=None, observations=observations,
            exception=RuntimeError("deadline for a prompt"), redact=tuple(request.prompt for request in rows))
        return CpuMeasuredBatchOutcome(**common, process=process, status="failed", failure_phase="transport", failure=failure)
    policy = resource_policy()
    receipt = CpuBatchReceipt(batch_digest=batch, request_digests=common["request_digests"],
        checkpoint_sha256="d" * 64, checkpoint_size_bytes=256, model_digest=rows[0].model.digest,
        runtime_digest=bound.native_runtime_digest,
        results=tuple(CpuInferenceResult(request_digest=request.digest, checkpoint_sha256="d" * 64,
            checkpoint_size_bytes=256, input_tokens=(index,), logits=(2.0, -1.0) if index == 0 else (-1.0, 2.0),
            output_token_id=index, text=request.model.output_tokens[index]) for index, request in enumerate(rows)),
        telemetry=CpuTelemetry(elapsed_seconds=0.12, peak_rss_bytes=16 * 1024 * 1024,
            memory_enforcement=policy["memory_enforcement"], memory_limit_bytes=512 * 1024 * 1024,
            address_space_limit_bytes=512 * 1024 * 1024 if sys.platform == "linux" else None,
            rss_poll_interval_seconds=policy["rss_poll_interval_seconds"],
            rss_sampling_may_overshoot=policy["rss_sampling_may_overshoot"],
            cpu_limit_seconds=math.ceil(rows[0].limits.wall_seconds)))
    process = CpuProcessObservation(trigger="completed", dispatched=True, elapsed_to_trigger_seconds=0.1,
        total_seconds=0.12, cleanup_seconds=0.02, state_before_cleanup="exited", returncode_before_cleanup=0,
        returncode_after_cleanup=0, signal_attempted=True, signal_sent=False, signal_number=9,
        cleanup_state="completed", leader_reaped=True)
    return CpuMeasuredBatchOutcome(**common, process=process, status="completed", failure_phase=None, receipt=receipt)


@pytest.mark.parametrize("scope", ["search", "reference"])
@pytest.mark.parametrize("failed", [False, True])
def test_actual_measured_return_and_exact_charges_survive_outcome_replay(scope: str, failed: bool) -> None:
    bound = contract(scope)
    raw, observation = claim(bound), measured(bound, failed=failed)
    outcome = build_cpu_operation_outcome(bound, raw, measured=observation)
    assert outcome.operation_claim_digest == canonical_digest(raw)
    assert outcome.operation_input_digest == canonical_digest(raw["input"])
    assert outcome.reserved.to_dict() == {"proposals": 0, "materializations": 0, "inference_calls": 2,
                                        "input_tokens": 2, "output_tokens": 2}
    assert outcome.status == observation.status and outcome.measured == observation
    assert validate_cpu_operation_outcome(outcome.to_dict(), bound, raw) == outcome
    assert operation_requests(bound, raw) == requests()
    if failed:
        assert outcome.measured.failure.observations.returncode is None
        assert outcome.measured.process.returncode_after_cleanup == -9
        with pytest.raises(ValueError):
            operation_value(outcome)
        with pytest.raises(ValueError):
            build_cpu_operation_check(bound, raw, outcome)
    else:
        assert operation_value(outcome) == observation.receipt.to_dict()
        assert build_cpu_operation_check(bound, raw, outcome)["status"] == "accepted"


@pytest.mark.parametrize("kind,scope", [("materialize", "search"), ("proposal", "search"),
                                       ("checkpoint-verification", "reference")])
@pytest.mark.parametrize("failed", [False, True])
def test_noninference_returns_and_errors_have_no_invented_process_evidence(kind: str, scope: str, failed: bool) -> None:
    bound = contract(scope)
    raw = claim(bound, kind)
    outcome = build_cpu_operation_outcome(bound, raw, **({"exception": OSError("a prompt failed")}
        if failed else {"value": returned(raw)}))
    assert outcome.measured is None and outcome.reserved.to_dict() == raw["reserved"]
    assert operation_requests(bound, raw) == ()
    if failed:
        assert outcome.failure is not None and outcome.failure.native_journal_kind == "absent"
        assert outcome.failure.study_digest == canonical_digest(raw)
        assert all(value is None for value in outcome.failure.observations.to_dict().values())
        if kind == "proposal":
            assert "a prompt" not in outcome.failure.exceptions[0].message.text
    else:
        assert operation_value(outcome) == returned(raw)
        assert build_cpu_operation_check(bound, raw, outcome)["status"] == "accepted"
    assert validate_cpu_operation_outcome(outcome, bound, raw) == outcome


def test_completed_measured_return_remains_intact_when_native_postcheck_fails() -> None:
    bound = contract()
    raw = claim(bound)
    outcome = build_cpu_operation_outcome(bound, raw, measured=measured(bound))
    saved = outcome.to_dict()
    check = build_cpu_operation_check(bound, raw, outcome, exception=OSError("a prompt checkpoint drift"))
    assert outcome.to_dict() == saved and outcome.status == "completed"
    assert check["outcome_digest"] == outcome.digest and check["status"] == "rejected"
    assert check["failure"]["study_digest"] == canonical_digest(raw)
    assert check["failure"]["run_id"] == "operation-check-3"
    assert all(value is None for value in check["failure"]["observations"].values())
    assert check["failure"]["exceptions"][0]["message"]["text"] == ""
    assert validate_cpu_operation_check(check, bound, raw, outcome) == check


@pytest.mark.parametrize("kind", ["proposal", "materialize", "checkpoint-verification"])
def test_malformed_serializable_return_is_saved_before_rejected_callback(kind: str) -> None:
    bound = contract("reference" if kind == "checkpoint-verification" else "search")
    raw = claim(bound, kind)
    wrong = {"unexpected": "bounded raw return"}
    outcome = build_cpu_operation_outcome(bound, raw, value=wrong)
    assert operation_value(outcome) == wrong
    with pytest.raises(ValueError):
        build_cpu_operation_check(bound, raw, outcome)
    rejected = build_cpu_operation_check(bound, raw, outcome, exception=ValueError("native callback rejected value"))
    assert validate_cpu_operation_check(rejected, bound, raw, outcome) == rejected


def test_unknown_launch_or_missing_measurements_cannot_be_fabricated_as_failure() -> None:
    bound = contract()
    for values in ({}, {"exception": RuntimeError("launch outcome unknown")}, {"value": {}}):
        with pytest.raises(ValueError, match="unreturned measured inference"):
            build_cpu_operation_outcome(bound, claim(bound), **values)


@pytest.mark.parametrize("mutation", ["contract", "index", "kind", "label", "input", "previous", "reserved",
    "missing-meter", "bool-meter", "hidden-input", "default-omission", "request-checkpoint", "case-ids"])
def test_claim_tampering_or_inexact_reservations_reject(mutation: str) -> None:
    bound = contract()
    raw = claim(bound)
    outcome = build_cpu_operation_outcome(bound, raw, measured=measured(bound))
    altered = copy.deepcopy(raw)
    if mutation == "contract":
        altered["contract_digest"] = "0" * 64
    elif mutation in {"index", "label", "kind"}:
        altered[mutation] = {"index": 4, "label": "different", "kind": "materialize"}[mutation]
    elif mutation == "input":
        altered["input"]["requests"][0]["prompt"] = "prompt"
    elif mutation == "previous":
        altered["previous_result_digest"] = "1" * 64
    elif mutation == "reserved":
        altered["reserved"]["input_tokens"] = 3
    elif mutation == "missing-meter":
        del altered["reserved"]["proposals"]
    elif mutation == "bool-meter":
        altered["reserved"]["proposals"] = False
    elif mutation == "hidden-input":
        altered["input"]["expected_labels"] = ["yes", "no"]
    elif mutation == "default-omission":
        del altered["input"]["requests"][0]["decoding"]["seed"]
    elif mutation == "request-checkpoint":
        altered["input"]["requests"][1]["checkpoint_sha256"] = "0" * 64
    else:
        altered["input"]["case_ids"] = ["repeat", "repeat"]
    with pytest.raises(ValueError):
        validate_cpu_operation_outcome(outcome, bound, altered)


@pytest.mark.parametrize("kind,scope", [("materialize", "reference"), ("proposal", "reference"),
                                       ("checkpoint-verification", "search")])
def test_claim_cannot_cross_workflow_scope(kind: str, scope: str) -> None:
    bound = contract(scope)
    with pytest.raises(ValueError, match="scope"):
        operation_requests(bound, claim(bound, kind))


def test_individual_operation_cannot_exceed_predeclared_capacity() -> None:
    bound = contract().model_copy(update={"capacity": CpuExecutionUsage(proposals=1, materializations=1,
        inference_calls=1, input_tokens=1, output_tokens=1)})
    with pytest.raises(ValueError, match="capacity"):
        operation_requests(bound, claim(bound))


@pytest.mark.parametrize("mutation", ["status", "claim", "input", "reserved", "profile", "runtime", "contract",
    "telemetry", "gpu", "missing-default", "fake-value", "unknown-field"])
def test_outcome_tampering_rejects_independently(mutation: str) -> None:
    bound = contract()
    raw = claim(bound)
    outcome = build_cpu_operation_outcome(bound, raw, measured=measured(bound, failed=True))
    data = outcome.to_dict()
    if mutation == "status":
        data["status"] = "completed"
    elif mutation in {"claim", "input"}:
        data[f"operation_{'claim' if mutation == 'claim' else 'input'}_digest"] = "0" * 64
    elif mutation == "reserved":
        data["reserved"]["input_tokens"] = 1
    elif mutation in {"profile", "runtime"}:
        data["measured"]["profile_digest" if mutation == "profile" else "native_runtime_digest"] = "0" * 64
    elif mutation == "contract":
        data["measured"]["contract"]["max_message_bytes"] = 32
    elif mutation == "telemetry":
        data["measured"]["failure"]["observations"]["returncode"] = -9
    elif mutation == "gpu":
        data["gpu_execution_deferred"] = 1
    elif mutation == "missing-default":
        del data["retry_permitted"]
    elif mutation == "fake-value":
        data["value"] = {}
    else:
        data["undeclared"] = 1
    with pytest.raises(ValueError):
        validate_cpu_operation_outcome(data, bound, raw)


@pytest.mark.parametrize("mutation", ["contract", "claim", "outcome", "status", "redaction", "telemetry", "schema"])
def test_postcheck_binding_and_nullable_observations_reject_tampering(mutation: str) -> None:
    bound = contract()
    raw = claim(bound)
    outcome = build_cpu_operation_outcome(bound, raw, measured=measured(bound))
    check = build_cpu_operation_check(bound, raw, outcome, exception=ValueError("a prompt invalid"))
    if mutation in {"contract", "claim", "outcome"}:
        key = {"contract": "contract_digest", "claim": "operation_claim_digest", "outcome": "outcome_digest"}[mutation]
        check[key] = "0" * 64
    elif mutation == "status":
        check["status"] = "accepted"
    elif mutation == "redaction":
        check["failure"]["redaction_digest"] = "0" * 64
    elif mutation == "telemetry":
        check["failure"]["observations"]["returncode"] = 0
    else:
        check["schema_version"] = True
    with pytest.raises(ValueError):
        validate_cpu_operation_check(check, bound, raw, outcome)


def test_pure_replay_uses_saved_pins_without_files_or_current_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    bound, observation = contract(), measured(contract())
    raw = claim(bound)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("pure outcome capture/replay accessed runtime or filesystem")

    monkeypatch.setattr(module, "execution_runtime_identity", forbidden)
    monkeypatch.setattr(module, "measured_runtime_identity", forbidden)
    for name in ("read_bytes", "write_bytes", "read_text", "write_text", "mkdir"):
        monkeypatch.setattr(Path, name, forbidden)
    outcome = build_cpu_operation_outcome(bound, raw, measured=observation)
    check = build_cpu_operation_check(bound, raw, outcome, exception=OSError("postcheck drift"))
    assert validate_cpu_operation_outcome(outcome, bound, raw) == outcome
    assert validate_cpu_operation_check(check, bound, raw, outcome) == check


def test_contract_factory_pins_measured_and_native_identities(monkeypatch: pytest.MonkeyPatch) -> None:
    native = {"implementation": "native-fixed"}
    profile = {"native_runtime": native, "implementation": "measured-fixed"}
    monkeypatch.setattr(module, "measured_runtime_identity", lambda: profile)
    monkeypatch.setattr(module, "execution_implementation_digest", lambda: "f" * 64)
    identity = module.execution_runtime_identity()
    assert identity["native_runtime_digest"] == canonical_digest(native)
    assert identity["measured_profile_digest"] == canonical_digest(profile)
    value = module.build_cpu_execution_contract(scope="search", design_digest="a" * 64, workflow_digest="a" * 64,
        workflow_implementation_digest="9" * 64, capacity=contract().capacity)
    assert value.native_runtime_digest == canonical_digest(native) and value.measured_profile_digest == canonical_digest(profile)


@pytest.mark.parametrize("payload", [lambda: {"x": "z" * 70000}, lambda: {"x": float("nan")}, lambda: {"x": (1,)}])
def test_unserializable_or_oversized_boundary_cannot_be_manufactured_into_outcome(payload: Any) -> None:
    bound = contract()
    with pytest.raises(ValueError):
        build_cpu_operation_outcome(bound, claim(bound, "proposal"), value=payload())


def test_cyclic_claim_rejected_before_digest_or_dispatch() -> None:
    bound = contract()
    raw = claim(bound)
    raw["input"]["loop"] = raw
    with pytest.raises(ValueError, match="cycle"):
        operation_requests(bound, raw)


def _golden_execution_case(name: str) -> dict[str, Any]:
    bound = contract()
    check = None
    if name == "measured-inference-failed":
        raw = claim(bound)
        outcome = build_cpu_operation_outcome(bound, raw, measured=measured(bound, failed=True))
    elif name == "materialization-failed":
        raw = claim(bound, "materialize")
        outcome = build_cpu_operation_outcome(bound, raw, exception=OSError("materializer failed"))
    elif name == "returned-proposal-rejected":
        raw = claim(bound, "proposal")
        outcome = build_cpu_operation_outcome(bound, raw, value=returned(raw))
        check = build_cpu_operation_check(bound, raw, outcome, exception=ValueError("a prompt callback rejected"))
    else:
        raise AssertionError("unknown golden fixture case")
    return {"contract": bound.to_dict(), "contract_digest": bound.digest, "claim": raw,
            "claim_digest": canonical_digest(raw), "outcome": outcome.to_dict(), "outcome_digest": outcome.digest,
            "check": check, "check_digest": canonical_digest(check) if check is not None else None}


@pytest.mark.parametrize("name", ["measured-inference-failed", "materialization-failed", "returned-proposal-rejected"])
def test_checked_in_outcome_fixture_replays_exact_reservations_and_failure_boundaries(name: str) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "weight_reproduction" / "cpu_execution_outcomes.json").read_text())
    assert fixture["fixture_kind"] == "synthetic-pure-cpu-operation-outcomes-v1"
    assert fixture["model_execution_performed"] is False
    expected = fixture["cases"][name]
    assert _golden_execution_case(name) == expected
    bound = CpuExecutionContract.model_validate_json(json.dumps(expected["contract"]))
    outcome = validate_cpu_operation_outcome(expected["outcome"], bound, expected["claim"])
    assert outcome.digest == expected["outcome_digest"]
    assert outcome.reserved.to_dict() == expected["claim"]["reserved"]
    if expected["check"] is not None:
        assert validate_cpu_operation_check(expected["check"], bound, expected["claim"], outcome) == expected["check"]
        assert outcome.status == "completed" and expected["check"]["status"] == "rejected"
