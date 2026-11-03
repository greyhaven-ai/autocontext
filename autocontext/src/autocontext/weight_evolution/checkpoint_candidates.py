"""Typed CPU checkpoint candidates projected through the frozen fixture interfaces.

The JSON handle is a compatibility projection, never a free-form checkpoint
description. Actual tensor bytes are materialized during claimed edits and
verified before probes and when resuming completed session evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.adaptive_provider import ProviderSessionAdapter, parse_proposal_receipt
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, regular_file
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier, _unique_object
from autocontext.weight_evolution.session import EditingSession, SessionIntegrityError, _read_record
from autocontext.weight_evolution.session_data import SessionDataBundle
from autocontext.weight_evolution.session_models import (
    AdapterResult,
    EditOperation,
    InspectOperation,
    SessionAdapterRequest,
    SessionSpec,
)
from autocontext.weight_evolution.tensor_edits import (
    CheckpointContract,
    TensorEditProgram,
    apply_tensor_edit,
    tensor_edit_implementation_digest,
    verify_checkpoint_contract,
    verify_tensor_edit,
)

MAX_CANDIDATES = 8
MAX_MATERIALIZED_BYTES = 32 * 1024 * 1024
MAX_HANDLE_BYTES = 4096


def _encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class _DigestModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


class CheckpointCandidateRef(_DigestModel):
    schema_version: Literal[1] = 1
    representation: Literal["verified_cpu_tensor_reference"] = "verified_cpu_tensor_reference"
    candidate_digest: Digest
    input_sha256: Digest
    program_digest: Digest | None
    output_sha256: Digest
    output_size_bytes: int = Field(gt=0, le=4 * 1024 * 1024)

    @property
    def handle(self) -> str:
        return _encode(self.to_dict())


class TensorCandidateSpec(_DigestModel):
    candidate_id: Identifier = Field(max_length=128)
    contract: CheckpointContract
    program: TensorEditProgram | None = None
    output_sha256: Digest
    output_size_bytes: int = Field(gt=0, le=4 * 1024 * 1024)

    @model_validator(mode="after")
    def input_output_contract(self) -> Self:
        if self.program is None:
            if self.output_sha256 != self.contract.sha256 or self.output_size_bytes != self.contract.size_bytes:
                raise ValueError("baseline candidate must identify the exact input checkpoint")
        elif self.program.input_digest != self.contract.digest:
            raise ValueError("tensor candidate program belongs to a different input contract")
        elif self.output_sha256 == self.contract.sha256:
            raise ValueError("edited tensor candidate must change checkpoint bytes")
        return self

    @property
    def reference(self) -> CheckpointCandidateRef:
        return CheckpointCandidateRef(candidate_digest=self.digest, input_sha256=self.contract.sha256,
                                      program_digest=self.program.digest if self.program is not None else None,
                                      output_sha256=self.output_sha256, output_size_bytes=self.output_size_bytes)

    @property
    def handle(self) -> str:
        return self.reference.handle


class VerifiedTensorCandidate(_DigestModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["cpu_tensor_fixture"] = "cpu_tensor_fixture"
    model_execution_performed: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    reference: CheckpointCandidateRef
    contract_digest: Digest
    verification_digest: Digest
    tensor_content_digest: Digest
    changed_entries: int = Field(ge=0, le=4096)
    implementation_digest: Digest


class TensorOperationBinding(_DigestModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["cpu_tensor_fixture"] = "cpu_tensor_fixture"
    model_execution_performed: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    operation_digest: Digest
    operation_kind: Literal["edit", "probe"]
    checkpoint: VerifiedTensorCandidate


class TensorCandidateRegistry:
    """Bounded, path-independent catalog with verified content-addressed outputs."""

    def __init__(self, candidates: tuple[TensorCandidateSpec, ...], *, input_path: Path, output_dir: Path) -> None:
        if not 1 <= len(candidates) <= MAX_CANDIDATES:
            raise ValueError("tensor candidate registry exceeds its finite candidate bound")
        self.candidates = tuple(TensorCandidateSpec.model_validate_json(_encode(candidate.to_dict())) for candidate in candidates)
        if len({candidate.candidate_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("tensor registry repeats a candidate label")
        if len({candidate.digest for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("tensor registry repeats a candidate identity")
        baselines = [candidate for candidate in self.candidates if candidate.program is None]
        if len(baselines) != 1:
            raise ValueError("tensor registry requires exactly one baseline candidate")
        self.baseline = baselines[0]
        if any(candidate.contract.digest != self.baseline.contract.digest for candidate in self.candidates):
            raise ValueError("all tensor candidates must share one immutable base contract")
        if sum(candidate.output_size_bytes for candidate in self.candidates) > MAX_MATERIALIZED_BYTES:
            raise ValueError("tensor candidate registry exceeds its total output-byte bound")
        self.input_path = Path(input_path).absolute()
        self.output_dir = Path(output_dir).absolute()
        if any(path.is_symlink() for path in (self.output_dir, *self.output_dir.parents)):
            raise ValueError("tensor registry paths must not traverse symlinks")

    def provenance(self) -> dict[str, Any]:
        return {"schema_version": 1, "execution_kind": "cpu_tensor_fixture", "model_execution_performed": False,
                "gpu_execution_deferred": True, "candidate_specs": [candidate.to_dict() for candidate in self.candidates],
                "max_candidates": MAX_CANDIDATES, "max_materialized_bytes": MAX_MATERIALIZED_BYTES,
                "implementation": tensor_edit_implementation_digest(), "bridge_source": file_identity(Path(__file__))}

    @property
    def identity_digest(self) -> str:
        return canonical_digest(self.provenance())

    def parse_handle(self, handle: str) -> TensorCandidateSpec:
        if not isinstance(handle, str) or len(handle.encode("utf-8")) > MAX_HANDLE_BYTES:
            raise ValueError("checkpoint handle exceeds its bounded canonical JSON size")
        value = json.loads(handle, object_pairs_hook=_unique_object)
        reference = CheckpointCandidateRef.model_validate_json(_encode(value))
        if reference.handle != handle:
            raise ValueError("checkpoint handle must use its exact canonical JSON representation")
        candidate = next((item for item in self.candidates if item.digest == reference.candidate_digest), None)
        if candidate is None or candidate.reference.digest != reference.digest:
            raise ValueError("checkpoint handle is absent from or differs from the pinned registry")
        return candidate

    def checkpoint_path(self, handle: str) -> Path:
        candidate = self.parse_handle(handle)
        return self.input_path if candidate.program is None else self.output_dir / candidate.digest / "model.safetensors"

    def _verified(self, candidate: TensorCandidateSpec) -> VerifiedTensorCandidate:
        if candidate.program is None:
            contract = verify_checkpoint_contract(candidate.contract, self.input_path)
            content = canonical_digest([{"name": tensor.name, "dtype": tensor.dtype, "shape": list(tensor.shape),
                                         "sha256": tensor.sha256} for tensor in contract.tensors])
            implementation = tensor_edit_implementation_digest()
            verification = {"schema_version": 1, "stage": "verified_cpu_tensor_baseline",
                            "contract_digest": contract.digest, "input_sha256": contract.sha256,
                            "output_sha256": contract.sha256, "output_size_bytes": contract.size_bytes,
                            "tensor_content_digest": content, "implementation_digest": implementation}
            return VerifiedTensorCandidate(reference=candidate.reference, contract_digest=contract.digest,
                                           verification_digest=canonical_digest(verification), tensor_content_digest=content,
                                           changed_entries=0, implementation_digest=implementation)
        receipt = verify_tensor_edit(candidate.contract, candidate.program, input_path=self.input_path,
                                     output_dir=self.output_dir / candidate.digest)
        if receipt.output_sha256 != candidate.output_sha256 or receipt.output_size_bytes != candidate.output_size_bytes:
            raise ValueError("materialized checkpoint differs from the pinned candidate output")
        return VerifiedTensorCandidate(reference=candidate.reference, contract_digest=candidate.contract.digest,
                                       verification_digest=receipt.digest, tensor_content_digest=receipt.tensor_content_digest,
                                       changed_entries=receipt.changed_entries,
                                       implementation_digest=receipt.implementation_digest)

    def verify(self, handle: str) -> VerifiedTensorCandidate:
        candidate = self.parse_handle(handle)
        if candidate.program is None:
            return self._verified(candidate)
        lock = self._store_lock(create=False)
        if lock is None:
            return self._verified(candidate)  # Missing retained output fails without creating a store.
        with advisory_path_lock(lock):
            return self._verified(candidate)

    def _store_lock(self, *, create: bool) -> Path | None:
        root = self.output_dir
        if any(path.is_symlink() for path in (root, *root.parents)):
            raise ValueError("tensor registry paths must not traverse symlinks")
        if not root.exists():
            if not create:
                return None
            root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise ValueError("tensor candidate store must be a directory")
        lock = root / ".tensor-candidates.lock"
        if lock.exists() or lock.is_symlink():
            with regular_file(lock):
                pass
        return lock

    def _verify_existing_locked(self) -> tuple[VerifiedTensorCandidate, ...]:
        verified = [self._verified(self.baseline)]
        candidates = {candidate.digest: candidate for candidate in self.candidates if candidate.program is not None}
        locks = {".tensor-candidates.lock", *(f".{digest}.tensor-edit.lock" for digest in candidates)}
        for path in self.output_dir.iterdir():
            if path.name in locks:
                with regular_file(path):
                    pass
            elif path.name not in candidates or path.is_symlink() or not path.is_dir():
                raise ValueError("unexpected tensor candidate materialization entry")
            else:
                verified.append(self._verified(candidates[path.name]))
        return tuple(verified)

    def verify_existing(self) -> tuple[VerifiedTensorCandidate, ...]:
        """Verify stable published state, waiting for any cooperating writer."""
        lock = self._store_lock(create=False)
        if lock is None:
            return (self._verified(self.baseline),)
        with advisory_path_lock(lock):
            return self._verify_existing_locked()

    def materialize(self, handle: str) -> VerifiedTensorCandidate:
        candidate = self.parse_handle(handle)
        if candidate.program is None:
            self.verify_existing()
            return self._verified(candidate)
        self._verified(self.baseline)
        lock = self._store_lock(create=True)
        assert lock is not None
        with advisory_path_lock(lock):
            self._verify_existing_locked()
            directory = self.output_dir / candidate.digest
            if directory.exists() or directory.is_symlink():
                return self._verified(candidate)
            apply_tensor_edit(candidate.contract, candidate.program, input_path=self.input_path, output_dir=directory)
            return self._verified(candidate)


class TensorSessionAdapter:
    """Materialize typed W references inside claimed edits before practice probes."""

    execution_kind = "fixture"

    def __init__(self, delegate: ProviderSessionAdapter, registry: TensorCandidateRegistry) -> None:
        if type(delegate) is not ProviderSessionAdapter or type(registry) is not TensorCandidateRegistry:
            raise ValueError("tensor sessions require the pinned offline adapter and typed registry")
        self.delegate = delegate
        self.registry = registry

    @property
    def identity_digest(self) -> str:
        return canonical_digest({"adapter": "verified-cpu-tensor-session-v1", "delegate": self.delegate.identity_digest,
                                 "registry": self.registry.identity_digest})

    def _current(self, request: SessionAdapterRequest) -> TensorCandidateSpec:
        current = self.registry.parse_handle(request.candidate.state.weight_descriptor)
        if self.delegate.mechanism != "W" and current.digest != self.registry.baseline.digest:
            raise ValueError("C/D sessions must retain the exact baseline checkpoint")
        return current

    def _binding(self, request: SessionAdapterRequest, checkpoint: VerifiedTensorCandidate) -> str:
        if isinstance(request.operation, InspectOperation):
            raise ValueError("proposal inspections retain their typed provider receipt")
        return _encode(TensorOperationBinding(operation_digest=request.operation.digest,
                                               operation_kind=request.operation.kind, checkpoint=checkpoint).to_dict())

    def execute(self, request: SessionAdapterRequest) -> AdapterResult:
        request = SessionAdapterRequest.model_validate_json(_encode(request.to_dict()))
        self.registry.verify_existing()
        current = self._current(request)
        # Even proposals cannot continue from missing or altered current bytes.
        checkpoint = self.registry.verify(current.handle)
        operation = request.operation
        if isinstance(operation, InspectOperation):
            result = self.delegate.execute(request)
            proposal = parse_proposal_receipt(result.message, self.delegate.mechanism)
            if self.delegate.mechanism == "W":
                assert proposal.proposal.weight_descriptor is not None
                self.registry.parse_handle(proposal.proposal.weight_descriptor)
            return result
        if isinstance(operation, EditOperation):
            result = self.delegate.execute(request)
            if self.delegate.mechanism == "W":
                if operation.weight_descriptor is None:
                    raise ValueError("W edits require a typed checkpoint reference")
                checkpoint = self.registry.materialize(operation.weight_descriptor)
            return AdapterResult.model_validate_json(_encode({**result.to_dict(), "message": self._binding(request, checkpoint)}))
        result = self.delegate.execute(request)
        return AdapterResult.model_validate_json(_encode({**result.to_dict(), "message": self._binding(request, checkpoint)}))

    def verify_session_materializations(self, session_output_dir: Path, *, data: SessionDataBundle) -> dict[str, Any]:
        """Replay M6 and verify retained checkpoint bindings without creating outputs.

        Proposed handles need registry membership, not materialization. Every
        successful edit/probe needs its original verified bytes and exact binding.
        Missing checkpoints are never reconstructed during completed replay.
        """
        self.registry.verify_existing()
        manifest = _read_record(session_output_dir / "manifest.json")
        spec = SessionSpec.model_validate_json(_encode(manifest.get("spec")))
        if spec.mechanism != self.delegate.mechanism or spec.baseline.base_checkpoint.content != self.registry.baseline.handle:
            raise SessionIntegrityError("session baseline/mechanism differs from the typed checkpoint registry")
        session = EditingSession(spec=spec, data=data, output_dir=session_output_dir, adapter=self)
        snapshot = session.snapshot()
        for index, receipt in enumerate(snapshot["operations"], 1):
            claim = _read_record(session_output_dir / "claims" / f"{index:08d}.json")
            request = SessionAdapterRequest.model_validate_json(_encode(claim["request"]))
            if request.digest != receipt["request_digest"] or request.operation.digest != receipt["operation_digest"]:
                raise SessionIntegrityError("retained tensor request differs from verified session history")
            current = self._current(request)
            self.registry.verify(current.handle)
            if receipt["status"] != "completed":
                continue
            saved = _read_record(session_output_dir / "results" / f"{index:08d}.json")
            result = AdapterResult.model_validate_json(_encode(saved))
            if result.digest != receipt["result_digest"] or result.operation_digest != request.operation.digest:
                raise SessionIntegrityError("retained tensor result differs from verified session history")
            if isinstance(request.operation, InspectOperation):
                proposal = parse_proposal_receipt(result.message, self.delegate.mechanism)
                if self.delegate.mechanism == "W":
                    assert proposal.proposal.weight_descriptor is not None
                    self.registry.parse_handle(proposal.proposal.weight_descriptor)
                continue
            target = current.handle
            if isinstance(request.operation, EditOperation) and self.delegate.mechanism == "W":
                if request.operation.weight_descriptor is None:
                    raise SessionIntegrityError("retained W edit lacks its checkpoint reference")
                target = request.operation.weight_descriptor
            verified = self.registry.verify(target)
            if result.message != self._binding(request, verified):
                raise SessionIntegrityError("retained tensor operation binding differs from actual checkpoint verification")
        return snapshot
