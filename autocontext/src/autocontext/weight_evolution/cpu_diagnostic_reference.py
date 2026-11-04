"""Exhaustive practice probes with a distinct, durable diagnostic journal.

Checkpoints are independently verified in the immutable source search store.
Only bounded CPU requests leave this coordinator; no proposal, selection,
confirmation, audit, or materialization operation is available here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, regular_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateSpec
from autocontext.weight_evolution.cpu_campaign import _inventory, _safe
from autocontext.weight_evolution.cpu_diagnostic_models import DIAGNOSTIC_METERS, CpuDiagnosticReference, CpuDiagnosticSpec
from autocontext.weight_evolution.cpu_model import verify_model_checkpoint
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest
from autocontext.weight_evolution.cpu_runner import CpuBatchReceipt, run_cpu_batch, runtime_identity, validate_cpu_receipt
from autocontext.weight_evolution.cpu_search import _existing_lock, _ReadOnlyRegistry
from autocontext.weight_evolution.cpu_study import CpuCase, _score
from autocontext.weight_evolution.session import _clear_staging, _immutable_json, _read_record, _same


class CpuDiagnosticReferenceError(ValueError):
    """A reference input or retained proof is invalid."""


class CpuDiagnosticReferenceBlockedError(CpuDiagnosticReferenceError):
    """A failed or uncertain dispatch remains charged and cannot be retried."""


class _NeedExecution(Exception):
    pass


def _cost(**values: int) -> dict[str, int]:
    return {meter: values.get(meter, 0) for meter in DIAGNOSTIC_METERS}


class _Journal:
    def __init__(self, spec: CpuDiagnosticSpec, reference: CpuDiagnosticReference, root: Path, *, replay: bool) -> None:
        self.spec, self.reference, self.root, self.replay = spec, reference, root, replay
        self.index = 0
        self.available = len(list((root / "claims").iterdir()))
        self.spent = _cost()
        self.previous = "0" * 64
        self.was_complete = (root / "complete.json").exists()

    def perform(self, kind: str, label: str, inputs: dict[str, Any], reserved: dict[str, int],
                execute: Callable[[], dict[str, Any]], validate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        if set(reserved) != set(DIAGNOSTIC_METERS) or any(type(v) is not int or v < 0 for v in reserved.values()):
            raise CpuDiagnosticReferenceError("invalid reference reservation")
        spent = {meter: self.spent[meter] + reserved[meter] for meter in DIAGNOSTIC_METERS}
        if any(value > self.reference.required_usage[meter] for meter, value in spent.items()):
            raise CpuDiagnosticReferenceError("reference exhausted its separate diagnostic budget")
        claim = {"schema_version": 1, "diagnostic_digest": self.spec.digest,
                 "reference_digest": self.reference.digest, "runtime_digest": self.spec.runtime_digest,
                 "index": self.index, "kind": kind, "label": label, "input": inputs, "reserved": reserved,
                 "previous_result_digest": self.previous}
        claim_path = self.root / "claims" / f"{self.index:08d}.json"
        result_path = self.root / "results" / claim_path.name
        if self.index < self.available:
            _same(_read_record(claim_path), claim, "reference claim differs from exhaustive replay")
            if not result_path.exists():
                raise CpuDiagnosticReferenceBlockedError("uncertain reference claim remains charged; retry forbidden")
            result = _read_record(result_path)
        else:
            if self.replay:
                raise _NeedExecution
            _immutable_json(claim_path, claim)
            try:
                value = execute()
                validate(value)
            except Exception as exc:
                _immutable_json(result_path, {"claim_digest": canonical_digest(claim), "status": "failed",
                                              "error_type": type(exc).__name__})
                raise CpuDiagnosticReferenceBlockedError("failed reference claim remains charged; retry forbidden") from exc
            result = {"claim_digest": canonical_digest(claim), "status": "completed", "value": value}
            _immutable_json(result_path, result)
        if result.get("claim_digest") != canonical_digest(claim):
            raise CpuDiagnosticReferenceError("reference result is not bound to its claim")
        if result.get("status") == "failed":
            if (set(result) != {"claim_digest", "status", "error_type"}
                    or not isinstance(result["error_type"], str) or self.index != self.available - 1):
                raise CpuDiagnosticReferenceError("failed reference is not a valid terminal operation")
            raise CpuDiagnosticReferenceBlockedError("failed reference claim remains charged; retry forbidden")
        if (set(result) != {"claim_digest", "status", "value"} or result["status"] != "completed"
                or not isinstance(result["value"], dict)):
            raise CpuDiagnosticReferenceError("invalid reference result")
        value = result["value"]
        validate(value)
        self.index += 1
        self.spent, self.previous = spent, canonical_digest(result)
        return value


def _prepare(root: Path, manifest: dict[str, Any], *, write: bool, operation_count: int) -> None:
    # Bound traversal before any directory listing, cleanup, or publication.
    _inventory(root)
    if write:
        _clear_staging(root)
    entries = {path.name for path in root.iterdir()}
    allowed = {".cpu-reference.lock", "manifest.json", "claims", "results", "complete.json"}
    if entries - allowed or ("manifest.json" not in entries and entries - {".cpu-reference.lock"}):
        raise CpuDiagnosticReferenceError("reference directory contains unowned evidence")
    if "manifest.json" in entries:
        _same(_read_record(root / "manifest.json"), manifest, "reference manifest changed")
    elif not write:
        raise CpuDiagnosticReferenceError("reference manifest missing")
    else:
        _immutable_json(root / "manifest.json", manifest)
    for name in ("claims", "results"):
        path = root / name
        _safe(path)
        if write:
            path.mkdir(exist_ok=True)
            _clear_staging(path)
        if not path.is_dir():
            raise CpuDiagnosticReferenceError("reference journal directory missing")
    # The shared inventory traversal bounds entries before sorting or hashing.
    _inventory(root)
    claims = {path.name for path in (root / "claims").iterdir()}
    results = {path.name for path in (root / "results").iterdir()}
    if (len(claims) > operation_count or claims != {f"{i:08d}.json" for i in range(len(claims))}
            or not results <= claims or results != {f"{i:08d}.json" for i in range(len(results))}
            or len(claims) - len(results) > 1):
        raise CpuDiagnosticReferenceError("reference journal is not a bounded contiguous prefix")
    if "complete.json" in entries and (results != claims or len(claims) != operation_count):
        raise CpuDiagnosticReferenceError("completed reference is missing retained evidence")


def _workflow(journal: _Journal, registry: _ReadOnlyRegistry, cases: tuple[CpuCase, ...]) -> dict[str, Any]:
    reference = journal.reference
    checkpoint_seals = {}
    for candidate in reference.candidates:
        def verify(candidate: TensorCandidateSpec = candidate) -> dict[str, Any]:
            verified = registry.verify(candidate.handle)
            verify_model_checkpoint(reference.model, registry.checkpoint_path(candidate.handle),
                                    expected_sha256=candidate.output_sha256,
                                    expected_size_bytes=candidate.output_size_bytes)
            return verified.to_dict()

        def validate_seal(raw: dict[str, Any], verify: Callable[[], dict[str, Any]] = verify) -> None:
            _same(raw, verify(), "source checkpoint seal changed")

        raw = journal.perform("checkpoint-verification", f"checkpoint-{candidate.candidate_id}",
                              {"candidate_digest": candidate.digest}, _cost(materializations=1), verify,
                              validate_seal)
        checkpoint_seals[candidate.candidate_id] = canonical_digest(raw)
    scores = {}
    semantic_receipts = {}
    evidence_receipts = {}
    for option in (None, *reference.options):
        option_id = option.option_id if option is not None else "A"
        candidate = next((c for c in reference.candidates if option is not None and c.candidate_id == option.candidate_id),
                         reference.baseline)
        decoding = option.decoding if option is not None and option.decoding is not None else reference.baseline_decoding
        prefix = option.context if option is not None and option.context is not None else ""
        requests = tuple(CpuInferenceRequest(
            request_id=f"reference-{option_id}-{index}", model=reference.model,
            checkpoint_sha256=candidate.output_sha256, checkpoint_size_bytes=candidate.output_size_bytes,
            prompt=" ".join(filter(None, (prefix, case.prompt))), decoding=decoding, limits=reference.limits,
        ) for index, case in enumerate(reference.practice))

        def validate(raw: dict[str, Any], requests: tuple[CpuInferenceRequest, ...] = requests,
                     candidate: TensorCandidateSpec = candidate) -> None:
            registry.verify(candidate.handle)
            receipt = CpuBatchReceipt.model_validate_json(json.dumps(raw))
            _same(raw, receipt.to_dict(), "reference receipt changed its typed representation")
            validate_cpu_receipt(receipt, requests, journal.spec.runtime_digest)

        def execute(requests: tuple[CpuInferenceRequest, ...] = requests,
                    candidate: TensorCandidateSpec = candidate) -> dict[str, Any]:
            registry.verify(candidate.handle)
            return run_cpu_batch(requests, checkpoint_path=registry.checkpoint_path(candidate.handle)).to_dict()

        inputs = {"option_id": option_id, "candidate_digest": candidate.digest,
                  "requests": [request.to_dict() for request in requests],
                  "practice_cases_digest": reference.practice_cases_digest}
        raw = journal.perform("inference", option_id, inputs,
                              _cost(inference_calls=len(requests), output_tokens=len(requests),
                                    input_tokens=sum(len(request.prompt.split()) for request in requests)), execute, validate)
        receipt = CpuBatchReceipt.model_validate_json(json.dumps(raw))
        scores[option_id] = _score(cases, receipt)
        semantic_receipts[option_id], evidence_receipts[option_id] = receipt.semantic_digest, receipt.digest
    _same(journal.spent, reference.required_usage, "exhaustive reference coverage or charges differ")
    summary = {"schema_version": 1, "execution_kind": "exhaustive_cpu_practice_reference", "device": "cpu",
               "model_execution_performed": True, "gpu_execution_deferred": True, "promotion_eligible": False,
               "benchmark_result_claimed": False, "diagnostic_digest": journal.spec.digest,
               "reference_digest": reference.digest, "runtime_digest": journal.spec.runtime_digest,
               "scores": scores, "charged_usage": journal.spent, "checkpoint_seals": checkpoint_seals,
               "receipt_digests": semantic_receipts, "scope": "practice-only-no-selection-or-heldout-evaluation"}
    summary["semantic_digest"] = canonical_digest(summary)
    summary["receipt_evidence_digests"] = evidence_receipts
    summary["journal_digest"] = journal.previous
    path = journal.root / "complete.json"
    if path.exists():
        _same(_read_record(path), summary, "reference summary differs from replayed evidence")
    elif not journal.replay:
        _immutable_json(path, summary)
    return summary


def _run(spec: CpuDiagnosticSpec, reference: CpuDiagnosticReference, *, checkpoint_path: Path, materializations: Path,
         cases: tuple[CpuCase, ...], output_dir: Path, execute: bool) -> dict[str, Any]:
    if (reference not in spec.references
            or canonical_digest([case.to_dict() for case in cases]) != reference.practice_cases_digest
            or any(case.role != "practice" for case in cases)
            or [(c.case_id, c.prompt) for c in cases] != [(c.case_id, c.prompt) for c in reference.practice]):
        raise CpuDiagnosticReferenceError("reference scorer must use exactly the pinned practice cases")
    if canonical_digest(runtime_identity()) != spec.runtime_digest:
        raise CpuDiagnosticReferenceError("reference CPU runtime differs from the frozen source")
    for path in (output_dir, checkpoint_path, materializations):
        _safe(path.absolute())
    root = output_dir.resolve()
    if any(root == source or root.is_relative_to(source) or source.is_relative_to(root)
           for source in (checkpoint_path.resolve(), materializations.resolve())):
        raise CpuDiagnosticReferenceError("reference output overlaps its source artifacts")
    registry = _ReadOnlyRegistry(reference.candidates, input_path=checkpoint_path, output_dir=materializations)
    registry.verify_existing()
    manifest = {"schema_version": 1, "diagnostic_digest": spec.digest, "reference_digest": reference.digest,
                "runtime_digest": spec.runtime_digest, "practice_cases_digest": reference.practice_cases_digest}
    if execute:
        root.mkdir(parents=True, exist_ok=True)
    elif not (root / "complete.json").is_file():
        raise CpuDiagnosticReferenceError("read-only reference verification requires completed evidence")
    lock = root / ".cpu-reference.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock) if execute else _existing_lock(lock):
        _prepare(root, manifest, write=execute, operation_count=len(reference.candidates) + len(reference.options) + 1)
        replay = _Journal(spec, reference, root, replay=True)
        complete = None
        try:
            complete = _workflow(replay, registry, cases)
        except _NeedExecution:
            if not execute or replay.was_complete:
                raise CpuDiagnosticReferenceError("reference is missing claimed evidence") from None
        if replay.index != replay.available:
            raise CpuDiagnosticReferenceError("reference retains claims beyond its verified prefix")
        if complete is not None and replay.was_complete:
            return complete
        if not execute:
            raise CpuDiagnosticReferenceError("reference completion marker missing")
        return _workflow(_Journal(spec, reference, root, replay=False), registry, cases)


def run_cpu_diagnostic_reference(spec: CpuDiagnosticSpec, reference: CpuDiagnosticReference, *, checkpoint_path: Path,
                                 materializations: Path, cases: tuple[CpuCase, ...], output_dir: Path) -> dict[str, Any]:
    return _run(spec, reference, checkpoint_path=checkpoint_path, materializations=materializations,
                cases=cases, output_dir=output_dir, execute=True)


def verify_cpu_diagnostic_reference(spec: CpuDiagnosticSpec, reference: CpuDiagnosticReference, *, checkpoint_path: Path,
                                    materializations: Path, cases: tuple[CpuCase, ...], output_dir: Path) -> dict[str, Any]:
    return _run(spec, reference, checkpoint_path=checkpoint_path, materializations=materializations,
                cases=cases, output_dir=output_dir, execute=False)
