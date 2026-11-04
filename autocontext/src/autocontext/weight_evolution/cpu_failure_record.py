"""Pure capture and replay validation for separately sealed CPU failure evidence."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import TypeAdapter

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateSpec
from autocontext.weight_evolution.cpu_failure_models import (
    CpuFailureCheckpointIdentity,
    CpuFailureContract,
    CpuFailureExceptionNode,
    CpuFailureObservations,
    CpuFailureRecord,
    CpuFailureRequestIdentity,
    CpuFailureText,
    exception_graph_has_cycle,
)
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest
from autocontext.weight_evolution.models import Digest


def _contract(value: CpuFailureContract) -> CpuFailureContract:
    return CpuFailureContract.model_validate(value.model_dump(warnings=False))


def _clip(text: str, maximum: int) -> tuple[str, bool, bool]:
    # Slice characters first so encoding an arbitrarily long exception string
    # does not allocate its full UTF-8 representation.
    prefix = text[:maximum + 1]
    encoded = prefix.encode("utf-8", errors="replace")
    replaced = encoded.decode("utf-8") != prefix
    return encoded[:maximum].decode("utf-8", errors="ignore"), len(text) > maximum or len(encoded) > maximum, replaced


def _text(value: str, maximum: int, redact: tuple[str, ...], *, rendering_failed: bool = False,
          encoding_replaced: bool = False) -> CpuFailureText:
    # Include enough overlap to redact a prompt that crosses the clipping edge.
    overlap = max((len(prompt) for prompt in redact), default=0)
    bounded = value[:maximum + overlap + 1]
    redacted = False
    for prompt in sorted(set(redact), key=lambda item: (-len(item), item)):
        if prompt and prompt in bounded:
            bounded = bounded.replace(prompt, "[redacted-prompt]")
            redacted = True
    clipped, truncated, replaced = _clip(bounded, maximum)
    return CpuFailureText(text=clipped, truncated=truncated or len(value) > maximum + overlap + 1,
                          redacted=redacted, encoding_replaced=encoding_replaced or replaced,
                          rendering_failed=rendering_failed)


def capture_observations(
    contract: CpuFailureContract, *, returncode: int | None = None, signal: int | None = None,
    elapsed_seconds: float | None = None, peak_rss_bytes: int | None = None,
    stdout: bytes | None = None, stderr: bytes | None = None, redact: tuple[str, ...] = (),
) -> CpuFailureObservations:
    """Bound explicitly supplied measurements without inferring absent fields."""
    contract = _contract(contract)
    if len(redact) > 32 or any(type(value) is not str or len(value.encode()) > 8192 for value in redact):
        raise ValueError("failure redaction inputs exceed the CPU request bounds")

    def stream(raw: bytes | None) -> CpuFailureText | None:
        if raw is None:
            return None
        if type(raw) is not bytes:
            raise ValueError("observed streams must be bytes")
        overlap = max((len(prompt.encode()) for prompt in redact), default=0)
        bound = contract.max_stream_bytes + overlap + 1
        retained = raw[:bound]
        try:
            value = retained.decode("utf-8")
            replaced = False
        except UnicodeDecodeError:
            value, replaced = retained.decode("utf-8", errors="replace"), True
        result = _text(value, contract.max_stream_bytes, redact, encoding_replaced=replaced)
        if len(raw) > bound and not result.truncated:
            result = result.model_copy(update={"truncated": True})
        return result

    return CpuFailureObservations(returncode=returncode, signal=signal, elapsed_seconds=elapsed_seconds,
                                   peak_rss_bytes=peak_rss_bytes, stdout=stream(stdout), stderr=stream(stderr))


def _exceptions(exception: BaseException, contract: CpuFailureContract,
                redact: tuple[str, ...]) -> tuple[CpuFailureExceptionNode, ...]:
    retained = [exception]
    identities = {id(exception): 0}
    nodes = []
    index = 0
    while index < len(retained):
        current = retained[index]
        kind = type(current)
        type_name, type_truncated, _ = _clip(kind.__name__, contract.max_type_bytes)
        module, module_truncated, _ = _clip(kind.__module__, contract.max_type_bytes)
        try:
            message = _text(str(current), contract.max_message_bytes, redact)
        except BaseException:
            message = CpuFailureText(text="[exception message unavailable]", rendering_failed=True)
        edges: dict[str, Any] = {}
        for name in ("cause", "context"):
            target = BaseException.__getattribute__(current, f"__{name}__")
            truncated = False
            target_id = None
            if target is not None:
                if id(target) in identities:
                    target_id = identities[id(target)]
                elif len(retained) < contract.max_exception_nodes:
                    target_id = len(retained)
                    identities[id(target)] = target_id
                    retained.append(target)
                else:
                    truncated = True
            edges[f"{name}_id"], edges[f"{name}_truncated"] = target_id, truncated
        nodes.append(CpuFailureExceptionNode(
            node_id=index, exception_type=type_name, exception_module=module,
            type_truncated=type_truncated, module_truncated=module_truncated, message=message,
            suppress_context=BaseException.__getattribute__(current, "__suppress_context__"), **edges,
        ))
        index += 1
    return tuple(nodes)


def _bindings(
    *, study_digest: str, runtime_digest: str, terminal_claim: dict[str, Any] | None,
    terminal_result: dict[str, Any] | None, contract: CpuFailureContract,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    fields: dict[str, Any] = {"terminal_claim_digest": None, "terminal_result_digest": None, "terminal_status": "absent",
                              "native_journal_kind": "absent", "diagnostic_digest": None,
                              "operation_index": None, "operation_kind": None, "operation_label": None,
                              "operation_input_digest": None, "candidate_digest": None, "requests": (), "checkpoints": (),
                              "retained_error_type": None, "retained_type_truncated": False}
    if terminal_claim is None:
        if terminal_result is not None:
            raise ValueError("terminal result requires its exact claim")
        return fields, ()
    reference = "reference_digest" in terminal_claim
    identity_keys = {"diagnostic_digest", "reference_digest"} if reference else {"study_digest"}
    identity_key = "reference_digest" if reference else "study_digest"
    result_binding_key = "claim_digest" if reference else "operation_digest"
    if (set(terminal_claim) != {"schema_version", *identity_keys, "runtime_digest", "index", "kind", "label", "input",
                               "reserved", "previous_result_digest"}
            or type(terminal_claim["schema_version"]) is not int or terminal_claim["schema_version"] != 1
            or terminal_claim[identity_key] != study_digest or terminal_claim["runtime_digest"] != runtime_digest
            or not isinstance(terminal_claim["input"], dict)):
        raise ValueError("terminal CPU claim differs from its exact child/runtime binding")
    if reference:
        fields["diagnostic_digest"] = TypeAdapter(Digest).validate_python(terminal_claim["diagnostic_digest"])
    fields["native_journal_kind"] = "reference" if reference else "search"
    digest = canonical_digest(terminal_claim)
    kind, inputs = terminal_claim["kind"], terminal_claim["input"]
    fields.update(terminal_claim_digest=digest, terminal_status="uncertain", operation_index=terminal_claim["index"],
                  operation_kind=kind, operation_label=terminal_claim["label"], operation_input_digest=canonical_digest(inputs))
    prompts: tuple[str, ...] = ()
    if kind == "inference":
        expected_inputs = ({"candidate_digest", "option_id", "practice_cases_digest", "requests"} if reference else
                           {"candidate_digest", "case_ids", "requests"})
        if set(inputs) != expected_inputs or not isinstance(inputs["requests"], list):
            raise ValueError("terminal inference input has an invalid request inventory")
        if not 1 <= len(inputs["requests"]) <= 32:
            raise ValueError("terminal inference input exceeds its request bound")
        requests = tuple(CpuInferenceRequest.model_validate_json(json.dumps(raw)) for raw in inputs["requests"])
        if canonical_digest(inputs["requests"]) != canonical_digest([request.to_dict() for request in requests]):
            raise ValueError("terminal inference requests changed their exact typed representation")
        if len({request.request_id for request in requests}) != len(requests):
            raise ValueError("terminal inference request IDs repeat")
        if not reference and (not isinstance(inputs["case_ids"], list) or len(inputs["case_ids"]) != len(requests)):
            raise ValueError("terminal inference case/request counts differ")
        if reference:
            TypeAdapter(Digest).validate_python(inputs["practice_cases_digest"])
            if inputs["option_id"] != terminal_claim["label"]:
                raise ValueError("terminal reference inference label differs from its option identity")
        first = requests[0]
        if any((request.model, request.checkpoint_sha256, request.checkpoint_size_bytes, request.limits) !=
               (first.model, first.checkpoint_sha256, first.checkpoint_size_bytes, first.limits) for request in requests):
            raise ValueError("terminal CPU inference requests must share model/checkpoint/limits")
        fields["candidate_digest"] = inputs["candidate_digest"]
        fields["requests"] = tuple(CpuFailureRequestIdentity(
            request_id=request.request_id, request_digest=request.digest, model_digest=request.model.digest,
            decoding_digest=request.decoding.digest, checkpoint_sha256=request.checkpoint_sha256,
            checkpoint_size_bytes=request.checkpoint_size_bytes, declared_limits=request.limits,
        ) for request in requests)
        fields["checkpoints"] = (CpuFailureCheckpointIdentity(sha256=first.checkpoint_sha256,
                                                               size_bytes=first.checkpoint_size_bytes),)
        prompts = tuple(request.prompt for request in requests)
    elif kind == "materialize":
        if reference or set(inputs) != {"candidate"}:
            raise ValueError("terminal materialization input has invalid fields")
        candidate = TensorCandidateSpec.model_validate_json(json.dumps(inputs["candidate"]))
        if canonical_digest(inputs["candidate"]) != candidate.digest:
            raise ValueError("terminal candidate changed its exact typed representation")
        fields["candidate_digest"] = candidate.digest
        fields["checkpoints"] = (CpuFailureCheckpointIdentity(sha256=candidate.output_sha256,
                                                               size_bytes=candidate.output_size_bytes),)
    elif kind == "checkpoint-verification":
        if not reference or set(inputs) != {"candidate_digest"}:
            raise ValueError("terminal checkpoint verification differs from its native reference input")
        fields["candidate_digest"] = inputs["candidate_digest"]
    elif kind != "proposal" or reference:
        raise ValueError("unknown terminal CPU operation kind")
    if terminal_result is not None:
        if terminal_result.get(result_binding_key) != digest:
            raise ValueError("terminal result does not bind the exact terminal claim")
        status = terminal_result.get("status")
        if status == "failed":
            if (set(terminal_result) != {result_binding_key, "status", "error_type"}
                    or type(terminal_result["error_type"]) is not str or not terminal_result["error_type"]):
                raise ValueError("invalid terminal CPU failure result")
            name, truncated, _ = _clip(terminal_result["error_type"], contract.max_type_bytes)
            fields.update(retained_error_type=name, retained_type_truncated=truncated)
        elif (status != "completed" or set(terminal_result) != {result_binding_key, "status", "value"}
              or not isinstance(terminal_result["value"], dict)):
            raise ValueError("invalid terminal CPU result")
        fields.update(terminal_result_digest=canonical_digest(terminal_result), terminal_status=status)
    return fields, prompts


def build_cpu_failure_record(
    contract: CpuFailureContract, *, supervisor_digest: str, run_id: str, study_digest: str, runtime_digest: str,
    terminal_claim: dict[str, Any] | None, terminal_result: dict[str, Any] | None,
    exception: BaseException | None = None, observations: CpuFailureObservations | None = None,
) -> CpuFailureRecord:
    """Capture bounded observed evidence without dispatch, file I/O or root-cause guesses."""
    contract = _contract(contract)
    bindings, redact = _bindings(study_digest=study_digest, runtime_digest=runtime_digest,
                                 terminal_claim=terminal_claim, terminal_result=terminal_result, contract=contract)
    if exception is not None and not isinstance(exception, BaseException):
        raise ValueError("live failure observation requires an exception object")
    nodes = _exceptions(exception, contract, redact) if exception is not None else ()
    captured = CpuFailureObservations.model_validate((observations or CpuFailureObservations()).model_dump(warnings=False))
    streams = {}
    for name in ("stdout", "stderr"):
        old = getattr(captured, name)
        if old is not None:
            text = _text(old.text, contract.max_stream_bytes, redact, rendering_failed=old.rendering_failed,
                         encoding_replaced=old.encoding_replaced)
            streams[name] = text.model_copy(update={"truncated": old.truncated or text.truncated,
                                                     "redacted": old.redacted or text.redacted})
    captured = captured.model_copy(update=streams)
    kind: Literal["live-exception", "retained-class-only", "observation-lost"] = (
        "live-exception" if nodes else "retained-class-only" if bindings["retained_error_type"] is not None
        else "observation-lost")
    return CpuFailureRecord(
        contract=contract, contract_digest=contract.digest, supervisor_digest=supervisor_digest, run_id=run_id,
        study_digest=study_digest, runtime_digest=runtime_digest, observation_kind=kind, exceptions=nodes,
        exception_chain_truncated=any(node.cause_truncated or node.context_truncated for node in nodes),
        exception_cycle_detected=exception_graph_has_cycle(nodes), observations=captured, **bindings,
    )


def validate_cpu_failure_record(
    record: CpuFailureRecord, contract: CpuFailureContract, *, supervisor_digest: str, run_id: str,
    study_digest: str, runtime_digest: str, terminal_claim: dict[str, Any] | None, terminal_result: dict[str, Any] | None,
) -> CpuFailureRecord:
    """Revalidate a sealed observation against the exact retained terminal inputs.

    Recorded exception text is observed evidence, not recreated on replay. The
    enclosing supervisor journal authenticates those bytes and their publication.
    """
    contract = _contract(contract)
    record = CpuFailureRecord.model_validate(record.model_dump(warnings=False))
    bindings, prompts = _bindings(study_digest=study_digest, runtime_digest=runtime_digest,
                                  terminal_claim=terminal_claim, terminal_result=terminal_result, contract=contract)
    expected = {"contract": contract.to_dict(), "contract_digest": contract.digest,
                "supervisor_digest": supervisor_digest, "run_id": run_id,
                "study_digest": study_digest, "runtime_digest": runtime_digest,
                **{name: [value.to_dict() for value in values] if name in {"requests", "checkpoints"} else values
                   for name, values in bindings.items()}}
    actual = record.to_dict()
    if any(canonical_digest(actual[name]) != canonical_digest(value) for name, value in expected.items()):
        raise ValueError("failure record differs from its contract, supervisor or exact terminal binding")
    texts = [node.message.text for node in record.exceptions]
    texts.extend(value.text for value in (record.observations.stdout, record.observations.stderr) if value is not None)
    if any(prompt and prompt in text for prompt in prompts for text in texts):
        raise ValueError("failure evidence must not retain raw request prompts")
    return record
