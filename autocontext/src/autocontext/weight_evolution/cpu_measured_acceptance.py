"""Two predeclared CPU batches with immutable, one-attempt parity evidence.

This fixed integration check compares native receipt semantics. It is neither a
search campaign nor an experiment that can diagnose a historical worker failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.util.file_lock import _lock, _unlock, advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.cpu_failure_v2_models import CpuFailureContractV2
from autocontext.weight_evolution.cpu_measured_models import CpuMeasuredBatchOutcome
from autocontext.weight_evolution.cpu_measured_runner import (
    measured_runtime_identity,
    run_measured_cpu_batch,
    validate_measured_outcome,
)
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest, CpuModelContract
from autocontext.weight_evolution.cpu_runner import (
    CpuBatchReceipt,
    CpuRunnerError,
    run_cpu_batch,
    runtime_identity,
    validate_cpu_receipt,
)
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier
from autocontext.weight_evolution.session import _STAGING_NAME, _clear_staging, _immutable_json, _read_record, _same

PROFILES: tuple[Literal["legacy-v1"], Literal["measured-v2"]] = ("legacy-v1", "measured-v2")
METERS = ("inference_calls", "input_tokens", "output_tokens")
MAX_RECORD_BYTES = 1024 * 1024
_LOCK = ".cpu-measured.lock"


class _Pinned(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "gpu_execution_deferred", "promotion_eligible", mode="before", check_fields=False)
    @classmethod
    def literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        if type(value) is not (int if info.field_name == "schema_version" else bool):
            raise ValueError("incorrect acceptance literal type")
        return value


class CpuMeasuredAcceptanceSpec(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["bounded-cpu-profile-parity-v1"] = "bounded-cpu-profile-parity-v1"
    acceptance_id: Identifier = Field(max_length=96)
    profiles: tuple[Literal["legacy-v1"], Literal["measured-v2"]] = PROFILES
    requests: tuple[CpuInferenceRequest, ...] = Field(min_length=1, max_length=4)
    native_runtime_digest: Digest
    measured_runtime_digest: Digest
    implementation_digest: Digest
    failure_contract: CpuFailureContractV2 = Field(default_factory=CpuFailureContractV2)
    dispatch_rule: Literal["both-profiles-predeclared-one-claim-one-attempt-no-retry"] = (
        "both-profiles-predeclared-one-claim-one-attempt-no-retry"
    )
    reservation_rule: Literal["all-claims-charged-in-full-without-refunds"] = "all-claims-charged-in-full-without-refunds"
    parity_rule: Literal["native-receipt-semantic-digest-excluding-telemetry"] = (
        "native-receipt-semantic-digest-excluding-telemetry"
    )
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False

    @model_validator(mode="after")
    def fixed_requests(self) -> Self:
        if self.requests != _requests(len(self.requests)):
            raise ValueError("acceptance requires its fixed checkpoint and practice request schedule")
        return self

    @property
    def batch_capacity(self) -> dict[str, int]:
        return {"inference_calls": len(self.requests), "input_tokens": sum(len(r.prompt.split()) for r in self.requests),
                "output_tokens": len(self.requests)}

    @property
    def required_capacity(self) -> dict[str, int]:
        return {key: 2 * value for key, value in self.batch_capacity.items()}


class _LegacyOutcome(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["native-cpu-class-only-outcome-v1"] = "native-cpu-class-only-outcome-v1"
    native_runtime_digest: Digest
    batch_digest: Digest
    status: Literal["completed", "failed"]
    receipt: CpuBatchReceipt | None = None
    error_type: Literal["CpuRunnerError"] | None = None

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        if self.status == "completed":
            if self.receipt is None or self.error_type is not None:
                raise ValueError("completed native outcome requires only its receipt")
        elif self.receipt is not None or self.error_type is None:
            raise ValueError("failed native outcome retains only the known error class")
        return self


def _encode(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _checkpoint_bytes() -> bytes:
    header = {"projection.bias": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]},
              "projection.weight": {"dtype": "F32", "shape": [2, 2], "data_offsets": [8, 24]}}
    raw = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    return struct.pack("<Q", len(raw)) + raw + struct.pack("<ffffff", 0.0, 0.0, 2.0, -1.0, -1.0, 2.0)


def _requests(count: int) -> tuple[CpuInferenceRequest, ...]:
    checkpoint = _checkpoint_bytes()
    model = CpuModelContract(vocabulary=("red", "blue"), output_tokens=("yes", "no"))
    prompts = ("red", "blue", "red red blue", "blue blue red")
    return tuple(CpuInferenceRequest(request_id=f"practice-{index + 1:02d}", model=model,
        checkpoint_sha256=hashlib.sha256(checkpoint).hexdigest(), checkpoint_size_bytes=len(checkpoint), prompt=prompt)
        for index, prompt in enumerate(prompts[:count]))


def measured_acceptance_implementation_digest() -> str:
    package = Path(__file__).parent
    return canonical_digest({"acceptance": file_identity(Path(__file__)),
        "journal": file_identity(package / "session.py"), "profile": measured_runtime_identity()})


def _safe(path: Path) -> Path:
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("symlink acceptance paths are forbidden")
    return path.resolve()


def _paths(*paths: Path) -> tuple[Path, ...]:
    resolved = tuple(_safe(path) for path in paths)
    for index, path in enumerate(resolved):
        if any(path.is_relative_to(other) or other.is_relative_to(path) for other in resolved[:index]):
            raise ValueError("acceptance bundle and output must not overlap")
    return resolved


def _entries(path: Path, maximum: int, *, staging: bool = False) -> set[str]:
    names: set[str] = set()
    staged = 0
    with os.scandir(path) as entries:
        for entry in entries:
            item = Path(entry.path)
            _safe(item)
            if staging and _STAGING_NAME.fullmatch(entry.name):
                staged += 1
                if staged > 16:
                    raise ValueError("too many acceptance publication staging files")
                file_identity(item, max_bytes=MAX_RECORD_BYTES)
                continue
            if len(names) >= maximum:
                raise ValueError("acceptance directory exceeds its bounded inventory")
            names.add(entry.name)
    return names


def _identity(raw: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}


def build_cpu_measured_acceptance(
    directory: Path, *, acceptance_id: str = "cpu-measured-parity-v1", request_count: int = 4,
) -> CpuMeasuredAcceptanceSpec:
    """Declare two fixed CPU batches; building performs no model inference."""
    directory, = _paths(directory)
    if type(request_count) is not int or not 1 <= request_count <= 4:
        raise ValueError("acceptance request_count must be an integer in 1..4")
    if directory.exists():
        raise ValueError("acceptance requires a fresh bundle directory")
    runtime = {"legacy-v1": runtime_identity(), "measured-v2": measured_runtime_identity()}
    spec = CpuMeasuredAcceptanceSpec(acceptance_id=acceptance_id, requests=_requests(request_count),
        native_runtime_digest=canonical_digest(runtime["legacy-v1"]),
        measured_runtime_digest=canonical_digest(runtime["measured-v2"]),
        implementation_digest=measured_acceptance_implementation_digest())
    artifacts = {"acceptance.json": _encode(spec.to_dict()), "runtime.json": _encode(runtime),
                 "model.safetensors": _checkpoint_bytes()}
    lock = {"schema_version": 1, "kind": "bounded-cpu-acceptance-bundle-v1",
            "artifacts": {name: _identity(raw) for name, raw in artifacts.items()}}
    directory.mkdir(parents=True)
    for name, raw in artifacts.items():
        (directory / name).write_bytes(raw)
    (directory / "bundle.lock.json").write_bytes(_encode(lock))
    return spec


def _load(directory: Path) -> tuple[CpuMeasuredAcceptanceSpec, dict[str, Any]]:
    if _entries(directory, 4) != {"acceptance.json", "runtime.json", "model.safetensors", "bundle.lock.json"}:
        raise ValueError("acceptance bundle inventory changed")
    lock = load_small_json(directory / "bundle.lock.json", max_bytes=4096)
    expected: dict[str, Any] = {"schema_version": 1, "kind": "bounded-cpu-acceptance-bundle-v1", "artifacts": {}}
    for name, maximum in (("acceptance.json", 65536), ("runtime.json", 131072), ("model.safetensors", 4096)):
        identity = file_identity(directory / name, max_bytes=maximum)
        expected["artifacts"][name] = {key: identity[key] for key in ("sha256", "size_bytes")}
    _same(lock, expected, "acceptance asset pin changed")
    raw = load_small_json(directory / "acceptance.json", max_bytes=65536)
    spec = CpuMeasuredAcceptanceSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "acceptance typed representation changed")
    if expected["artifacts"]["model.safetensors"] != _identity(_checkpoint_bytes()):
        raise ValueError("acceptance checkpoint differs from its fixed construction")
    runtime = load_small_json(directory / "runtime.json", max_bytes=131072)
    _same(runtime, {"legacy-v1": runtime_identity(), "measured-v2": measured_runtime_identity()},
          "acceptance runtime or implementation changed")
    if (spec.native_runtime_digest != canonical_digest(runtime["legacy-v1"])
            or spec.measured_runtime_digest != canonical_digest(runtime["measured-v2"])
            or spec.implementation_digest != measured_acceptance_implementation_digest()):
        raise ValueError("acceptance implementation or runtime pin changed")
    return spec, runtime


def validate_cpu_measured_acceptance(directory: Path) -> dict[str, Any]:
    directory, = _paths(directory)
    spec, _ = _load(directory)
    return {"acceptance_digest": spec.digest, "profiles": list(PROFILES), "requests_per_batch": len(spec.requests),
            "required_capacity": spec.required_capacity, "failure_contract_digest": spec.failure_contract.digest,
            "runtime_matches": True, "model_execution_performed": False, "gpu_execution_deferred": True}


@contextmanager
def _read_lock(path: Path) -> Iterator[None]:
    with regular_file(path) as stream:
        _lock(stream.fileno())
        try:
            yield
        finally:
            _unlock(stream.fileno())


def _read(path: Path) -> dict[str, Any]:
    file_identity(path, max_bytes=MAX_RECORD_BYTES)
    return _read_record(path)


def _manifest(spec: CpuMeasuredAcceptanceSpec, runtime: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "acceptance_digest": spec.digest, "spec": spec.to_dict(), "runtime": runtime}


def _preflight(root: Path, manifest: dict[str, Any]) -> None:
    if not root.exists():
        return
    names = _entries(root, 7, staging=True)
    if names - {_LOCK, "manifest.json", "claims", "outcomes", "results", "report.json"}:
        raise ValueError("acceptance output contains unowned evidence")
    if "manifest.json" not in names:
        if names - {_LOCK}:
            raise ValueError("acceptance evidence has no manifest")
    else:
        _same(_read(root / "manifest.json"), manifest, "acceptance manifest changed")
    if _LOCK in names:
        with regular_file(root / _LOCK):
            pass


def _claim(spec: CpuMeasuredAcceptanceSpec, index: int, previous: str) -> dict[str, Any]:
    return {"schema_version": 1, "acceptance_digest": spec.digest, "index": index, "profile": PROFILES[index],
            "request_digests": [request.digest for request in spec.requests], "reserved_capacity": spec.batch_capacity,
            "previous_result_digest": previous}


def _batch_digest(spec: CpuMeasuredAcceptanceSpec) -> str:
    return canonical_digest({"request_digests": [r.digest for r in spec.requests],
                             "runtime_digest": spec.native_runtime_digest})


def _validate_outcome(raw: dict[str, Any], spec: CpuMeasuredAcceptanceSpec, index: int,
                      checkpoint: Path | None) -> tuple[str, CpuBatchReceipt | None]:
    if set(raw) != {"profile", "outcome"} or raw["profile"] != PROFILES[index]:
        raise ValueError("acceptance outcome profile changed")
    if index == 0:
        legacy = _LegacyOutcome.model_validate_json(json.dumps(raw["outcome"]))
        _same(raw["outcome"], legacy.to_dict(), "native outcome representation changed")
        if (legacy.native_runtime_digest != spec.native_runtime_digest
                or legacy.batch_digest != _batch_digest(spec)):
            raise ValueError("native acceptance outcome request or runtime binding changed")
        if legacy.receipt is not None:
            validate_cpu_receipt(legacy.receipt, spec.requests, spec.native_runtime_digest)
        return legacy.status, legacy.receipt
    measured = CpuMeasuredBatchOutcome.model_validate_json(json.dumps(raw["outcome"]))
    _same(raw["outcome"], measured.to_dict(), "measured outcome representation changed")
    validate_measured_outcome(measured, spec.requests, checkpoint_path=checkpoint, contract=spec.failure_contract,
        profile_digest=spec.measured_runtime_digest, native_runtime_digest=spec.native_runtime_digest)
    return measured.status, measured.receipt


def _result(claim: dict[str, Any], outcome: dict[str, Any] | None, status: str,
            receipt: CpuBatchReceipt | None) -> dict[str, Any]:
    return {"schema_version": 1, "claim_digest": canonical_digest(claim),
            "outcome_digest": canonical_digest(outcome) if outcome is not None else None,
            "status": status, "receipt_semantic_digest": receipt.semantic_digest if receipt is not None else None,
            "charged_capacity": claim["reserved_capacity"],
            "verified_usage": claim["reserved_capacity"] if status == "completed" else None}


def _prefix(root: Path, spec: CpuMeasuredAcceptanceSpec, checkpoint: Path) -> list[dict[str, Any]]:
    inventories = {name: _entries(root / name, 2, staging=True) if (root / name).exists() else set()
                   for name in ("claims", "outcomes", "results")}
    claims, outcomes, results = (inventories[name] for name in ("claims", "outcomes", "results"))
    if (claims != {f"{i:08d}.json" for i in range(len(claims))}
            or results != {f"{i:08d}.json" for i in range(len(results))}
            or not results <= claims or not outcomes <= claims or len(claims) - len(results) > 1):
        raise ValueError("acceptance journal is not a contiguous bounded prefix")
    if (root / "report.json").exists() and len(results) != 2:
        raise ValueError("sealed acceptance report is missing its terminal journal")
    prefix: list[dict[str, Any]] = []
    previous = "0" * 64
    for index in range(len(claims)):
        name = f"{index:08d}.json"
        claim = _claim(spec, index, previous)
        _same(_read(root / "claims" / name), claim, "acceptance claim changed")
        outcome = _read(root / "outcomes" / name) if name in outcomes else None
        status, receipt = _validate_outcome(outcome, spec, index, checkpoint) if outcome is not None else ("uncertain", None)
        result = _result(claim, outcome, status, receipt)
        published = name in results
        if published:
            _same(_read(root / "results" / name), result, "acceptance result differs from retained outcome")
        prefix.append({"claim": claim, "outcome": outcome, "result": result, "result_published": published})
        previous = canonical_digest(result)
    return prefix


def _report(spec: CpuMeasuredAcceptanceSpec, manifest: dict[str, Any], prefix: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    zero = {key: 0 for key in METERS}
    for index, profile in enumerate(PROFILES):
        entry = prefix[index] if index < len(prefix) else None
        result = entry["result"] if entry is not None else None
        rows.append({"profile": profile, "status": result["status"] if result else "not-started",
            "result_published": entry["result_published"] if entry else False,
            "outcome_digest": result["outcome_digest"] if result else None,
            "receipt_semantic_digest": result["receipt_semantic_digest"] if result else None,
            "reserved_capacity": spec.batch_capacity, "charged_capacity": spec.batch_capacity if entry else zero,
            "verified_usage": result["verified_usage"] if result else None})
    available = all(row["status"] == "completed" for row in rows)
    parity = rows[0]["receipt_semantic_digest"] == rows[1]["receipt_semantic_digest"] if available else None
    report = {"schema_version": 1, "report_kind": "bounded-cpu-profile-parity-v1", "acceptance_digest": spec.digest,
        "native_runtime_digest": spec.native_runtime_digest, "measured_runtime_digest": spec.measured_runtime_digest,
        "rows": rows, "status_counts": {status: sum(row["status"] == status for row in rows)
            for status in ("completed", "failed", "uncertain", "not-started")},
        "pending_results": sum(not entry["result_published"] for entry in prefix),
        "required_capacity": spec.required_capacity,
        "charged_capacity": {key: len(prefix) * value for key, value in spec.batch_capacity.items()},
        "verified_usage": {key: sum((row["verified_usage"] or zero)[key] for row in rows) for key in METERS},
        "parity_available": available, "semantic_parity": parity,
        "acceptance_passed": parity is True and all(row["result_published"] for row in rows),
        "ledger_digest": canonical_digest({"manifest": manifest, "prefix": prefix}),
        "gpu_execution_deferred": True, "promotion_eligible": False, "historical_failure_cause_recovered": False,
        "scope": "fixed-practice-cpu-batch-parity-not-search-quality-or-historical-cause"}
    return {**report, "semantic_digest": canonical_digest(report)}


def _dispatch(spec: CpuMeasuredAcceptanceSpec, index: int, checkpoint: Path) -> dict[str, Any]:
    if index == 1:
        measured = run_measured_cpu_batch(spec.requests, checkpoint_path=checkpoint, contract=spec.failure_contract)
        return {"profile": PROFILES[index], "outcome": measured.to_dict()}
    try:
        receipt = run_cpu_batch(spec.requests, checkpoint_path=checkpoint)
    except CpuRunnerError:
        legacy = _LegacyOutcome(native_runtime_digest=spec.native_runtime_digest,
            batch_digest=_batch_digest(spec), status="failed", error_type="CpuRunnerError")
    else:
        legacy = _LegacyOutcome(native_runtime_digest=spec.native_runtime_digest,
            batch_digest=_batch_digest(spec), status="completed", receipt=receipt)
    return {"profile": PROFILES[index], "outcome": legacy.to_dict()}


def _run(directory: Path, *, output_dir: Path, execute: bool) -> dict[str, Any]:
    directory, root = _paths(directory, output_dir)
    spec, runtime = _load(directory)
    manifest = _manifest(spec, runtime)
    _preflight(root, manifest)
    if execute:
        root.mkdir(parents=True, exist_ok=True)
    with advisory_path_lock(root / _LOCK) if execute else _read_lock(root / _LOCK):
        _preflight(root, manifest)
        if not (root / "manifest.json").exists():
            if not execute:
                raise ValueError("acceptance output manifest missing")
            _immutable_json(root / "manifest.json", manifest)
        checkpoint = directory / "model.safetensors"
        prefix = _prefix(root, spec, checkpoint)
        if execute:
            _clear_staging(root)
            for name in ("claims", "outcomes", "results"):
                (root / name).mkdir(exist_ok=True)
                _clear_staging(root / name)
            previous = "0" * 64
            for index in range(2):
                name = f"{index:08d}.json"
                if index < len(prefix):
                    result = prefix[index]["result"]
                    if not prefix[index]["result_published"]:
                        _immutable_json(root / "results" / name, result)
                else:
                    _load(directory)
                    claim = _claim(spec, index, previous)
                    _immutable_json(root / "claims" / name, claim)
                    outcome = _dispatch(spec, index, checkpoint)
                    # Preserve already captured measurements even if a mutable
                    # source/checkpoint changed during the accepted attempt.
                    # Saved bindings are checked now; current assets must pass
                    # again before any result or subsequent dispatch.
                    status, receipt = _validate_outcome(outcome, spec, index, None)
                    _immutable_json(root / "outcomes" / name, outcome)
                    _load(directory)
                    result = _result(claim, outcome, status, receipt)
                    _immutable_json(root / "results" / name, result)
                previous = canonical_digest(result)
            prefix = _prefix(root, spec, checkpoint)
        report = _report(spec, manifest, prefix)
        if (root / "report.json").exists():
            _same(_read(root / "report.json"), report, "acceptance report differs from retained evidence")
        elif execute:
            _immutable_json(root / "report.json", report)
        return report


def run_cpu_measured_acceptance(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    """Attempt each unclaimed profile once; never dispatch a retained claim."""
    return _run(directory, output_dir=output_dir, execute=True)


def report_cpu_measured_acceptance(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    """Verify complete or pending evidence without writes or worker execution."""
    return _run(directory, output_dir=output_dir, execute=False)
