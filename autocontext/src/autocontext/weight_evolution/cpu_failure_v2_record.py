"""Pure v2 evidence capture; redaction failures omit text instead of leaking it."""

from __future__ import annotations

import json
import unicodedata
from typing import Any, Literal

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_failure_models import CpuFailureContract
from autocontext.weight_evolution.cpu_failure_record import _bindings as _v1_bindings
from autocontext.weight_evolution.cpu_failure_record import _clip
from autocontext.weight_evolution.cpu_failure_v2_models import (
    CpuFailureCheckpointIdentityV2,
    CpuFailureContractV2,
    CpuFailureExceptionNodeV2,
    CpuFailureObservationsV2,
    CpuFailureRecordV2,
    CpuFailureRequestIdentityV2,
    CpuFailureTextV2,
    exception_graph_has_cycle,
)


def _contract(value: CpuFailureContractV2) -> CpuFailureContractV2:
    return CpuFailureContractV2.model_validate(value.model_dump(warnings=False))


def _normalize(value: str) -> tuple[str, bool]:
    valid = value.encode("utf-8", errors="replace").decode("utf-8")
    return unicodedata.normalize("NFC", valid), valid != value


def _redactions(redact: tuple[str, ...], *, maximum: int = 32) -> tuple[str, ...]:
    if type(redact) is not tuple or len(redact) > maximum:
        raise ValueError("failure redaction inventory exceeds its bounded tuple contract")
    terms = set()
    for term in redact:
        if type(term) is not str or not term or len(term) > 8192:
            raise ValueError("failure redaction terms must be nonempty bounded strings")
        normalized, _ = _normalize(term)
        if len(normalized.encode()) > 8192:
            raise ValueError("failure redaction term exceeds its UTF-8 byte bound")
        terms.add(normalized)
    return tuple(sorted(terms))


def _redaction_digest(contract: CpuFailureContractV2, terms: tuple[str, ...]) -> str:
    # Persist hashes, never the raw redaction inventory.
    return canonical_digest({"rule": contract.redaction_rule, "terms": [canonical_digest(term) for term in terms]})


def _text(value: str, maximum: int, terms: tuple[str, ...], *,
          encoding_replaced: bool = False) -> CpuFailureTextV2:
    # Four characters cover the maximum canonical Unicode decomposition of one
    # codepoint. The extra window finds secrets crossing the visible byte edge.
    bound = maximum + 4 * max(map(len, terms), default=0) + 8
    bounded, replaced = _normalize(value[:bound])
    source_truncated = len(value) > bound
    # Track the furthest match beginning at each position, then union overlapping
    # spans once. Sequential replace would miss overlaps or alter inserted markers.
    ends = [0] * len(bounded)
    for term in terms:
        position = bounded.find(term)
        while position >= 0:
            ends[position] = max(ends[position], position + len(term))
            position = bounded.find(term, position + 1)
    spans: list[tuple[int, int]] = []
    start = end = 0
    for index, limit in enumerate(ends):
        if limit:
            if not spans and end == 0:
                start, end = index, limit
            elif index <= end:
                end = max(end, limit)
            else:
                spans.append((start, end))
                start, end = index, limit
    if end:
        spans.append((start, end))
    parts: list[str] = []
    previous = 0
    for start, end in spans:
        parts.extend((bounded[previous:start], "[redacted]"))
        previous = end
    parts.append(bounded[previous:])
    rendered = "".join(parts)
    clipped, truncated, clip_replaced = _clip(rendered, maximum)
    # A marker can itself equal/contain a secret, and surrounding text can form
    # new matches across replacement boundaries. Empty omission is always safe.
    collision = any(term in clipped for term in terms)
    return CpuFailureTextV2(
        text="" if collision else clipped, truncated=truncated or source_truncated,
        redacted=bool(spans) or collision, encoding_replaced=encoding_replaced or replaced or clip_replaced,
        omitted=collision, omission_reason="redaction-collision" if collision else None,
    )


def capture_observations_v2(
    contract: CpuFailureContractV2, *, returncode: int | None = None, signal: int | None = None,
    elapsed_seconds: float | None = None, peak_rss_bytes: int | None = None,
    stdout: bytes | None = None, stderr: bytes | None = None, redact: tuple[str, ...] = (),
) -> CpuFailureObservationsV2:
    """Capture supplied measurements only; absent telemetry remains null."""
    contract, terms = _contract(contract), _redactions(redact)

    def stream(raw: bytes | None) -> CpuFailureTextV2 | None:
        if raw is None:
            return None
        if type(raw) is not bytes:
            raise ValueError("observed streams must be bytes")
        bound = contract.max_stream_bytes + 16 * max(map(len, terms), default=0) + 16
        retained = raw[:bound]
        try:
            decoded, replaced = retained.decode("utf-8"), False
        except UnicodeDecodeError:
            decoded, replaced = retained.decode("utf-8", errors="replace"), True
        captured = _text(decoded, contract.max_stream_bytes, terms, encoding_replaced=replaced)
        data = captured.model_dump()
        data["truncated"] = captured.truncated or len(raw) > bound
        return CpuFailureTextV2.model_validate(data)

    return CpuFailureObservationsV2(returncode=returncode, signal=signal, elapsed_seconds=elapsed_seconds,
                                   peak_rss_bytes=peak_rss_bytes, stdout=stream(stdout), stderr=stream(stderr))


def _bindings(
    contract: CpuFailureContractV2, *, study_digest: str, runtime_digest: str,
    terminal_claim: dict[str, Any] | None, terminal_result: dict[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    # The native immutable journal grammar is unchanged; only evidence capture is
    # versioned. Convert identities into the v2 schema after exact v1 binding.
    native_contract = CpuFailureContract(**{name: getattr(contract, name) for name in
        ("max_exception_nodes", "max_message_bytes", "max_stream_bytes", "max_type_bytes", "max_record_bytes")})
    bindings, prompts = _v1_bindings(study_digest=study_digest, runtime_digest=runtime_digest,
        terminal_claim=terminal_claim, terminal_result=terminal_result, contract=native_contract)
    bindings["requests"] = tuple(CpuFailureRequestIdentityV2.model_validate(value.model_dump())
                                 for value in bindings["requests"])
    bindings["checkpoints"] = tuple(CpuFailureCheckpointIdentityV2.model_validate(value.model_dump())
                                    for value in bindings["checkpoints"])
    return bindings, prompts


def _exceptions(exception: BaseException, contract: CpuFailureContractV2,
                terms: tuple[str, ...]) -> tuple[CpuFailureExceptionNodeV2, ...]:
    retained, identities, nodes = [exception], {id(exception): 0}, []
    for index, current in enumerate(retained):
        kind = type(current)
        name, name_truncated, _ = _clip(type.__getattribute__(kind, "__name__"), contract.max_type_bytes)
        module, module_truncated, _ = _clip(type.__getattribute__(kind, "__module__"), contract.max_type_bytes)
        try:
            rendered = str(current)
        except BaseException:
            message = CpuFailureTextV2(text="", rendering_failed=True, omitted=True, omission_reason="rendering-failed")
        else:
            message = _text(rendered, contract.max_message_bytes, terms)
        edges: dict[str, Any] = {}
        for edge in ("cause", "context"):
            target = BaseException.__getattribute__(current, f"__{edge}__")
            target_id, truncated = None, False
            if target is not None:
                if id(target) in identities:
                    target_id = identities[id(target)]
                elif len(retained) < contract.max_exception_nodes:
                    target_id = len(retained)
                    identities[id(target)] = target_id
                    retained.append(target)
                else:
                    truncated = True
            edges[edge + "_id"], edges[edge + "_truncated"] = target_id, truncated
        nodes.append(CpuFailureExceptionNodeV2(
            node_id=index, exception_type=name, exception_module=module, type_truncated=name_truncated,
            module_truncated=module_truncated, message=message,
            suppress_context=BaseException.__getattribute__(current, "__suppress_context__"), **edges))
    return tuple(nodes)


def build_cpu_failure_record_v2(
    contract: CpuFailureContractV2, *, supervisor_digest: str, run_id: str, study_digest: str, runtime_digest: str,
    terminal_claim: dict[str, Any] | None, terminal_result: dict[str, Any] | None,
    exception: BaseException | None = None, observations: CpuFailureObservationsV2 | None = None,
    redact: tuple[str, ...] = (),
) -> CpuFailureRecordV2:
    """Capture and validate one record; no I/O, dispatch, retries or inferred cause."""
    contract = _contract(contract)
    explicit = _redactions(redact)
    bindings, prompts = _bindings(contract, study_digest=study_digest, runtime_digest=runtime_digest,
                                  terminal_claim=terminal_claim, terminal_result=terminal_result)
    terms = _redactions((*explicit, *prompts), maximum=64)
    if exception is not None and not isinstance(exception, BaseException):
        raise ValueError("live failure observation requires an exception object")
    nodes = _exceptions(exception, contract, terms) if exception is not None else ()
    captured = CpuFailureObservationsV2.model_validate(
        (observations or CpuFailureObservationsV2()).model_dump(warnings=False))
    data = captured.model_dump()
    for name in ("stdout", "stderr"):
        previous = getattr(captured, name)
        if previous is None or previous.omitted:
            continue
        if previous.truncated and terms:
            # A previously clipped stream may contain only a secret prefix. Its
            # missing suffix cannot be reconstructed, so retain no ambiguous text.
            bounded = CpuFailureTextV2(text="", truncated=True, redacted=True,
                encoding_replaced=previous.encoding_replaced, rendering_failed=previous.rendering_failed,
                omitted=True, omission_reason="redaction-collision")
        else:
            bounded = _text(previous.text, contract.max_stream_bytes, terms,
                            encoding_replaced=previous.encoding_replaced)
            updated = bounded.model_dump()
            updated.update(truncated=previous.truncated or bounded.truncated,
                           redacted=previous.redacted or bounded.redacted,
                           rendering_failed=previous.rendering_failed)
            bounded = CpuFailureTextV2.model_validate(updated)
        data[name] = bounded.model_dump()
    captured = CpuFailureObservationsV2.model_validate(data)
    kind: Literal["live-exception", "retained-class-only", "observation-lost"] = (
        "live-exception" if nodes else "retained-class-only" if bindings["retained_error_type"] is not None
        else "observation-lost")
    record = CpuFailureRecordV2(
        contract=contract, contract_digest=contract.digest, redaction_digest=_redaction_digest(contract, terms),
        redaction_count=len(terms), supervisor_digest=supervisor_digest, run_id=run_id, study_digest=study_digest,
        runtime_digest=runtime_digest, observation_kind=kind, exceptions=nodes,
        exception_chain_truncated=any(node.cause_truncated or node.context_truncated for node in nodes),
        exception_cycle_detected=exception_graph_has_cycle(nodes), observations=captured, **bindings)
    return validate_cpu_failure_record_v2(record, contract, supervisor_digest=supervisor_digest, run_id=run_id,
        study_digest=study_digest, runtime_digest=runtime_digest, terminal_claim=terminal_claim,
        terminal_result=terminal_result, redact=redact)


def validate_cpu_failure_record_v2(
    record: CpuFailureRecordV2, contract: CpuFailureContractV2, *, supervisor_digest: str, run_id: str,
    study_digest: str, runtime_digest: str, terminal_claim: dict[str, Any] | None, terminal_result: dict[str, Any] | None,
    redact: tuple[str, ...] = (),
) -> CpuFailureRecordV2:
    """Validate exact bindings and redaction; past exception authenticity is external."""
    contract = _contract(contract)
    record = CpuFailureRecordV2.model_validate(record.model_dump(warnings=False))
    explicit = _redactions(redact)
    bindings, prompts = _bindings(contract, study_digest=study_digest, runtime_digest=runtime_digest,
                                  terminal_claim=terminal_claim, terminal_result=terminal_result)
    terms = _redactions((*explicit, *prompts), maximum=64)
    expected = {"contract": contract.to_dict(), "contract_digest": contract.digest,
                "redaction_digest": _redaction_digest(contract, terms), "redaction_count": len(terms),
                "supervisor_digest": supervisor_digest, "run_id": run_id,
                "study_digest": study_digest, "runtime_digest": runtime_digest,
                **{name: [value.to_dict() for value in values] if name in {"requests", "checkpoints"} else values
                   for name, values in bindings.items()}}
    actual = record.to_dict()
    if any(canonical_digest(actual[name]) != canonical_digest(value) for name, value in expected.items()):
        raise ValueError("v2 failure differs from its contract, redaction inventory or exact binding")
    texts = [node.message.text for node in record.exceptions]
    texts.extend(value.text for value in (record.observations.stdout, record.observations.stderr) if value is not None)
    if any(term in _normalize(text)[0] for term in terms for text in texts):
        raise ValueError("v2 failure text must not retain a redacted term")
    # JSON round trips must preserve the validated graph, omissions and byte caps.
    if CpuFailureRecordV2.model_validate_json(json.dumps(actual)).to_dict() != actual:
        raise ValueError("v2 failure serialization changed its typed representation")
    return record
