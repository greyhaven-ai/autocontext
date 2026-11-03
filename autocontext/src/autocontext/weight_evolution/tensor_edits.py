"""Bounded data-only sparse tensor edits with independent checkpoint verification.

Edits replace exact little-endian scalar bits. No arithmetic program, pickle,
downloaded code, model runtime, or GPU runs. M2's safetensors reader/writer and
finite-value checks authenticate complete tiny checkpoint inventories.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal, Self, cast

from pydantic import Field, model_validator

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import (
    CheckpointError,
    TensorFile,
    canonical_digest,
    ensure_finite,
    file_identity,
    load_small_json,
    regular_file,
    write_tensor_file,
)
from autocontext.weight_evolution.models import Digest, FrozenModel

MAX_TINY_CHECKPOINT_BYTES = 4 * 1024 * 1024
MAX_TENSORS = 64
MAX_REPLACEMENTS = 4096
MAX_EDIT_JSON_BYTES = 2 * 1024 * 1024
TensorDtype = Literal["BF16", "F16", "F32", "F64"]
_WIDTH = {"BF16": 2, "F16": 2, "F32": 4, "F64": 8}
_OUTPUT_FILES = {"model.safetensors", "receipt.json", "complete.json"}


class _DigestModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


class TensorContract(_DigestModel):
    name: str = Field(min_length=1, max_length=512)
    dtype: TensorDtype
    shape: tuple[int, ...] = Field(max_length=8)
    sha256: Digest
    editable: bool = False

    @model_validator(mode="after")
    def bounded_tensor(self) -> Self:
        if self.name == "__metadata__" or any(ord(char) < 32 for char in self.name):
            raise ValueError("invalid tensor name")
        if any(size <= 0 for size in self.shape) or math.prod(self.shape) * _WIDTH[self.dtype] > MAX_TINY_CHECKPOINT_BYTES:
            raise ValueError("tensor shape exceeds the tiny checkpoint byte cap")
        return self


class CheckpointContract(_DigestModel):
    schema_version: Literal[1] = 1
    format: Literal["safetensors"] = "safetensors"
    sha256: Digest
    size_bytes: int = Field(ge=10, le=MAX_TINY_CHECKPOINT_BYTES)
    tensors: tuple[TensorContract, ...] = Field(min_length=1, max_length=MAX_TENSORS)
    max_changed_entries: int = Field(default=MAX_REPLACEMENTS, gt=0, le=MAX_REPLACEMENTS)

    @model_validator(mode="after")
    def complete_inventory(self) -> Self:
        names = tuple(tensor.name for tensor in self.tensors)
        if names != tuple(sorted(set(names))):
            raise ValueError("checkpoint tensor names must be unique and sorted")
        if sum(math.prod(tensor.shape) * _WIDTH[tensor.dtype] for tensor in self.tensors) > self.size_bytes:
            raise ValueError("tensor inventory exceeds the pinned checkpoint size")
        return self


class TensorReplacement(_DigestModel):
    index: int = Field(ge=0, lt=MAX_TINY_CHECKPOINT_BYTES)
    value_hex: str = Field(pattern=r"^[0-9a-f]+$", min_length=4, max_length=16)


class TensorEdit(_DigestModel):
    name: str = Field(min_length=1, max_length=512)
    expected_sha256: Digest
    dtype: TensorDtype
    shape: tuple[int, ...] = Field(max_length=8)
    replacements: tuple[TensorReplacement, ...] = Field(min_length=1, max_length=MAX_REPLACEMENTS)

    @model_validator(mode="after")
    def bounded_replacements(self) -> Self:
        TensorContract(name=self.name, dtype=self.dtype, shape=self.shape, sha256=self.expected_sha256)
        indices = tuple(replacement.index for replacement in self.replacements)
        if indices != tuple(sorted(set(indices))) or indices[-1] >= math.prod(self.shape):
            raise ValueError("replacement indices must be unique, sorted, and inside the tensor")
        for replacement in self.replacements:
            if len(replacement.value_hex) != 2 * _WIDTH[self.dtype]:
                raise ValueError("replacement bit width differs from the declared dtype")
            ensure_finite(bytes.fromhex(replacement.value_hex), self.dtype)
        return self


class TensorEditProgram(_DigestModel):
    schema_version: Literal[1] = 1
    operation: Literal["sparse_replace_bits"] = "sparse_replace_bits"
    input_digest: Digest
    edits: tuple[TensorEdit, ...] = Field(min_length=1, max_length=MAX_TENSORS)

    @model_validator(mode="after")
    def bounded_program(self) -> Self:
        names = tuple(edit.name for edit in self.edits)
        if names != tuple(sorted(set(names))):
            raise ValueError("edited tensor names must be unique and sorted")
        if sum(len(edit.replacements) for edit in self.edits) > MAX_REPLACEMENTS:
            raise ValueError("program exceeds the total replacement cap")
        return self


class TensorChange(_DigestModel):
    name: str
    dtype: TensorDtype
    shape: tuple[int, ...]
    input_sha256: Digest
    output_sha256: Digest
    changed_entries: int = Field(ge=0, le=MAX_REPLACEMENTS)
    editable: bool


class TensorEditReceipt(_DigestModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    tensor_edit_performed: Literal[True] = True
    model_execution_performed: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    contract_digest: Digest
    program_digest: Digest
    input_sha256: Digest
    output_sha256: Digest
    output_size_bytes: int = Field(ge=10, le=MAX_TINY_CHECKPOINT_BYTES)
    tensor_content_digest: Digest
    tensors: tuple[TensorChange, ...] = Field(min_length=1, max_length=MAX_TENSORS)
    changed_entries: int = Field(gt=0, le=MAX_REPLACEMENTS)
    implementation_digest: Digest


def tensor_edit_implementation_digest() -> str:
    package = Path(__file__).parent
    return canonical_digest({name: file_identity(package / name, max_bytes=1024 * 1024)
                             for name in ("tensor_edits.py", "artifacts.py", "models.py")})


def _validated_contract(contract: CheckpointContract) -> CheckpointContract:
    return CheckpointContract.model_validate_json(json.dumps(contract.to_dict(), allow_nan=False))


def _verify_input(contract: CheckpointContract, input_path: Path) -> tuple[TensorFile, dict[str, bytes]]:
    identity = file_identity(input_path, max_bytes=MAX_TINY_CHECKPOINT_BYTES)
    if identity["sha256"] != contract.sha256 or identity["size_bytes"] != contract.size_bytes:
        raise CheckpointError("input checkpoint differs from its pinned size/hash")
    checkpoint = TensorFile(input_path)
    if set(checkpoint.tensors) != {tensor.name for tensor in contract.tensors}:
        raise CheckpointError("input checkpoint differs from the complete tensor inventory")
    payloads = {}
    for tensor in contract.tensors:
        info = checkpoint.tensors[tensor.name]
        if (info.dtype, info.shape) != (tensor.dtype, tensor.shape):
            raise CheckpointError("input tensor shape/dtype differs from its contract")
        payload = checkpoint.read(tensor.name)
        if hashlib.sha256(payload).hexdigest() != tensor.sha256:
            raise CheckpointError("input tensor bytes differ from their pin")
        payloads[tensor.name] = payload
    # Authenticate both sides of the read interval; every tensor is also pinned.
    if file_identity(input_path, max_bytes=MAX_TINY_CHECKPOINT_BYTES) != identity:
        raise CheckpointError("input checkpoint changed during verification")
    return checkpoint, payloads


def verify_checkpoint_contract(contract: CheckpointContract, input_path: Path) -> CheckpointContract:
    contract = _validated_contract(contract)
    _verify_input(contract, input_path)
    return contract


def build_checkpoint_contract(input_path: Path, *, editable_names: tuple[str, ...],
                              max_changed_entries: int = MAX_REPLACEMENTS) -> CheckpointContract:
    """Pin a trusted local fixture; this does not discover or download checkpoints."""
    identity = file_identity(input_path, max_bytes=MAX_TINY_CHECKPOINT_BYTES)
    checkpoint = TensorFile(input_path)
    if len(checkpoint.tensors) > MAX_TENSORS:
        raise CheckpointError("checkpoint exceeds its tensor-count cap")
    if len(set(editable_names)) != len(editable_names) or not set(editable_names) <= set(checkpoint.tensors):
        raise CheckpointError("editable tensor names must be unique existing tensors")
    tensors = tuple(TensorContract(name=name, dtype=checkpoint.tensors[name].dtype,  # type: ignore[arg-type]
                                   shape=checkpoint.tensors[name].shape,
                                   sha256=hashlib.sha256(checkpoint.read(name)).hexdigest(), editable=name in editable_names)
                    for name in sorted(checkpoint.tensors))
    contract = CheckpointContract(sha256=str(identity["sha256"]), size_bytes=cast(int, identity["size_bytes"]),
                                  tensors=tensors, max_changed_entries=max_changed_entries)
    return verify_checkpoint_contract(contract, input_path)


def _expected_tensors(contract: CheckpointContract, program: TensorEditProgram,
                      original: dict[str, bytes]) -> dict[str, bytes]:
    if program.input_digest != contract.digest:
        raise CheckpointError("edit program belongs to another input contract")
    if sum(len(edit.replacements) for edit in program.edits) > contract.max_changed_entries:
        raise CheckpointError("edit exceeds the contract's changed-entry budget")
    pins = {tensor.name: tensor for tensor in contract.tensors}
    expected = dict(original)
    for edit in program.edits:
        pin = pins.get(edit.name)
        if pin is None or not pin.editable:
            raise CheckpointError("edit targets an absent or protected tensor")
        if (edit.expected_sha256, edit.dtype, edit.shape) != (pin.sha256, pin.dtype, pin.shape):
            raise CheckpointError("edit tensor pin/shape/dtype differs from the input contract")
        patched = bytearray(original[edit.name])
        width = _WIDTH[edit.dtype]
        for replacement in edit.replacements:
            start = replacement.index * width
            bits = bytes.fromhex(replacement.value_hex)
            if patched[start:start + width] == bits:
                raise CheckpointError("edit contains an unchanged replacement")
            patched[start:start + width] = bits
        expected[edit.name] = bytes(patched)
        ensure_finite(expected[edit.name], edit.dtype)
    return expected


def _inspect_output(contract: CheckpointContract, program: TensorEditProgram, input_path: Path,
                    output_path: Path) -> TensorEditReceipt:
    _, original = _verify_input(contract, input_path)
    expected = _expected_tensors(contract, program, original)
    output_identity = file_identity(output_path, max_bytes=MAX_TINY_CHECKPOINT_BYTES)
    checkpoint = TensorFile(output_path)
    if set(checkpoint.tensors) != set(original):
        raise CheckpointError("output changed the checkpoint tensor inventory")
    records = []
    edits = {edit.name: edit for edit in program.edits}
    for pin in contract.tensors:
        info = checkpoint.tensors[pin.name]
        if (info.dtype, info.shape) != (pin.dtype, pin.shape):
            raise CheckpointError("output changed tensor shape/dtype")
        actual = checkpoint.read(pin.name)
        if actual != expected[pin.name]:
            raise CheckpointError("output differs from independently reconstructed tensor bytes")
        records.append(TensorChange(name=pin.name, dtype=pin.dtype, shape=pin.shape, input_sha256=pin.sha256,
                                    output_sha256=hashlib.sha256(actual).hexdigest(), editable=pin.editable,
                                    changed_entries=len(edits[pin.name].replacements) if pin.name in edits else 0))
    if file_identity(output_path, max_bytes=MAX_TINY_CHECKPOINT_BYTES) != output_identity:
        raise CheckpointError("output checkpoint changed during verification")
    _verify_input(contract, input_path)
    return TensorEditReceipt(
        contract_digest=contract.digest, program_digest=program.digest, input_sha256=contract.sha256,
        output_sha256=str(output_identity["sha256"]), output_size_bytes=cast(int, output_identity["size_bytes"]),
        tensor_content_digest=canonical_digest([{"name": row.name, "dtype": row.dtype, "shape": row.shape,
                                                "sha256": row.output_sha256} for row in records]),
        tensors=tuple(records), changed_entries=sum(row.changed_entries for row in records),
        implementation_digest=tensor_edit_implementation_digest(),
    )


def _safe_path(path: Path) -> None:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise CheckpointError("tensor edit paths must not traverse symlinks")


def _completion(receipt: TensorEditReceipt) -> dict[str, Any]:
    return {"schema_version": 1, "receipt_digest": receipt.digest, "contract_digest": receipt.contract_digest,
            "program_digest": receipt.program_digest, "output_sha256": receipt.output_sha256}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("xb") as stream:
        stream.write((json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())


def verify_tensor_edit(contract: CheckpointContract, program: TensorEditProgram, *, input_path: Path,
                       output_dir: Path) -> TensorEditReceipt:
    """Independently reconstruct all expected bytes; stored receipts are not authority."""
    contract = _validated_contract(contract)
    program = TensorEditProgram.model_validate_json(json.dumps(program.to_dict(), allow_nan=False))
    _safe_path(output_dir)
    if not output_dir.is_dir() or {path.name for path in output_dir.iterdir()} != _OUTPUT_FILES:
        raise CheckpointError("tensor edit output is incomplete or contains unexpected files")
    receipt = _inspect_output(contract, program, input_path, output_dir / "model.safetensors")
    saved = load_small_json(output_dir / "receipt.json", max_bytes=MAX_EDIT_JSON_BYTES)
    complete = load_small_json(output_dir / "complete.json", max_bytes=4096)
    if canonical_digest(saved) != receipt.digest or canonical_digest(complete) != canonical_digest(_completion(receipt)):
        raise CheckpointError("tensor edit receipt/completion differs from independently verified content")
    return receipt


def apply_tensor_edit(contract: CheckpointContract, program: TensorEditProgram, *, input_path: Path,
                      output_dir: Path) -> TensorEditReceipt:
    """Create a verified complete output, or verify an existing immutable result.

    An existing incomplete directory is never repaired. Publication is a staged
    directory rename; interrupted staging is removed without adopting evidence.
    """
    contract = _validated_contract(contract)
    program = TensorEditProgram.model_validate_json(json.dumps(program.to_dict(), allow_nan=False))
    root = output_dir.absolute()
    _safe_path(root)
    source, original = _verify_input(contract, input_path)
    expected = _expected_tensors(contract, program, original)
    root.parent.mkdir(parents=True, exist_ok=True)
    lock = root.parent / f".{root.name}.tensor-edit.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock):
        if root.exists() or root.is_symlink():
            return verify_tensor_edit(contract, program, input_path=input_path, output_dir=root)
        temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}-", dir=root.parent))
        try:
            write_tensor_file(temporary / "model.safetensors", tuple(source.tensors[pin.name] for pin in contract.tensors),
                              expected.__getitem__)
            receipt = _inspect_output(contract, program, input_path, temporary / "model.safetensors")
            _write_json(temporary / "receipt.json", receipt.to_dict())
            _write_json(temporary / "complete.json", _completion(receipt))
            verify_tensor_edit(contract, program, input_path=input_path, output_dir=temporary)
            os.rename(temporary, root)
            descriptor = os.open(root.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return receipt
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
