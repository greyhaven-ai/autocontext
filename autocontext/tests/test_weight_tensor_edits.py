from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import struct
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import TensorFile, TensorInfo, canonical_digest, write_tensor_file
from autocontext.weight_evolution.tensor_edits import (
    MAX_REPLACEMENTS,
    MAX_TINY_CHECKPOINT_BYTES,
    CheckpointContract,
    TensorContract,
    TensorEdit,
    TensorEditProgram,
    TensorReplacement,
    apply_tensor_edit,
    build_checkpoint_contract,
    verify_checkpoint_contract,
    verify_tensor_edit,
)


def bits(dtype: str, value: float) -> bytes:
    if dtype == "BF16":
        return struct.pack("<f", value)[2:]
    return struct.pack({"F16": "<e", "F32": "<f", "F64": "<d"}[dtype], value)


def write_checkpoint(path: Path, payloads: dict[str, tuple[str, tuple[int, ...], bytes]]) -> None:
    tensors = tuple(TensorInfo(name, dtype, shape, 0, len(data))
                    for name, (dtype, shape, data) in sorted(payloads.items()))
    write_tensor_file(path, tensors, lambda name: payloads[name][2])


@pytest.fixture
def base(tmp_path: Path) -> Path:
    path = tmp_path / "input.safetensors"
    payloads = {f"edit.{dtype}": (dtype, (2, 2), b"".join(bits(dtype, value) for value in (1.0, 2.0, 3.0, 4.0)))
                for dtype in ("BF16", "F16", "F32", "F64")}
    payloads["frozen.weight"] = ("F32", (2,), bits("F32", 9.0) + bits("F32", -2.0))
    write_checkpoint(path, payloads)
    return path


@pytest.fixture
def contract(base: Path) -> CheckpointContract:
    return build_checkpoint_contract(base, editable_names=tuple(f"edit.{dtype}" for dtype in ("BF16", "F16", "F32", "F64")))


@pytest.fixture
def program(contract: CheckpointContract) -> TensorEditProgram:
    return TensorEditProgram(input_digest=contract.digest, edits=tuple(
        TensorEdit(name=pin.name, expected_sha256=pin.sha256, dtype=pin.dtype, shape=pin.shape,
                   replacements=(TensorReplacement(index=1, value_hex=bits(pin.dtype, 6.0).hex()),))
        for pin in contract.tensors if pin.editable))


def test_actual_edits_verify_all_dtypes_and_preserve_untouched_tensor(
    base: Path, contract: CheckpointContract, program: TensorEditProgram, tmp_path: Path,
) -> None:
    original = base.read_bytes()
    output = tmp_path / "edited"
    receipt = apply_tensor_edit(contract, program, input_path=base, output_dir=output)
    assert verify_tensor_edit(contract, program, input_path=base, output_dir=output) == receipt
    assert base.read_bytes() == original
    assert receipt.changed_entries == 4 and len(receipt.tensors) == 5
    assert receipt.input_sha256 == hashlib.sha256(original).hexdigest()
    assert receipt.program_digest == program.digest and receipt.contract_digest == contract.digest
    assert receipt.output_sha256 == hashlib.sha256((output / "model.safetensors").read_bytes()).hexdigest()
    assert receipt.model_execution_performed is False and receipt.gpu_execution_deferred is True
    edited, source = TensorFile(output / "model.safetensors"), TensorFile(base)
    for tensor in receipt.tensors:
        assert (edited.tensors[tensor.name].dtype, edited.tensors[tensor.name].shape) == (tensor.dtype, tensor.shape)
        if tensor.editable:
            width = len(bits(tensor.dtype, 0.0))
            assert edited.read(tensor.name)[width:2 * width] == bits(tensor.dtype, 6.0)
            assert tensor.changed_entries == 1 and tensor.input_sha256 != tensor.output_sha256
        else:
            assert edited.read(tensor.name) == source.read(tensor.name)
            assert tensor.changed_entries == 0 and tensor.input_sha256 == tensor.output_sha256


def test_repeated_apply_only_verifies_and_independent_outputs_are_identical(
    base: Path, contract: CheckpointContract, program: TensorEditProgram, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.weight_evolution import tensor_edits

    a, b = tmp_path / "a", tmp_path / "b"
    first = apply_tensor_edit(contract, program, input_path=base, output_dir=a)
    assert apply_tensor_edit(contract, program, input_path=base, output_dir=b) == first
    assert {p.name: p.read_bytes() for p in a.iterdir()} == {p.name: p.read_bytes() for p in b.iterdir()}

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("existing complete output was rewritten")

    monkeypatch.setattr(tensor_edits, "write_tensor_file", forbidden)
    assert apply_tensor_edit(contract, program, input_path=base, output_dir=a) == first


@pytest.mark.parametrize("dtype", ["BF16", "F16", "F32", "F64"])
@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_nonfinite_replacement_bits_rejected(dtype: str, value: float) -> None:
    with pytest.raises(ValueError, match="nonfinite"):
        TensorEdit(name="weight", expected_sha256="0" * 64, dtype=dtype, shape=(1,),  # type: ignore[arg-type]
                   replacements=(TensorReplacement(index=0, value_hex=bits(dtype, value).hex()),))


@pytest.mark.parametrize("dtype", ["BF16", "F16", "F32", "F64"])
def test_nonfinite_input_rejected_before_contract_creation(tmp_path: Path, dtype: str) -> None:
    # Build a valid finite checkpoint, then mutate its raw scalar to bypass the
    # trusted writer. Contract creation must independently reject those bytes.
    path = tmp_path / "nonfinite.safetensors"
    write_checkpoint(path, {"weight": (dtype, (1,), bits(dtype, 1.0))})
    indexed = TensorFile(path)
    raw = bytearray(path.read_bytes())
    raw[indexed.data_start:] = bits(dtype, math.inf)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="nonfinite"):
        build_checkpoint_contract(path, editable_names=("weight",))


@pytest.mark.parametrize("indices", [(0, 0), (1, 0), (4,)])
def test_duplicate_unsorted_and_out_of_range_indices_rejected(indices: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="indices"):
        TensorEdit(name="weight", expected_sha256="0" * 64, dtype="F32", shape=(2, 2),
                   replacements=tuple(TensorReplacement(index=index, value_hex=bits("F32", 1.0).hex()) for index in indices))


@pytest.mark.parametrize("value_hex", ["0000", "0000000000000000", "0000000"])
def test_wrong_replacement_width_rejected(value_hex: str) -> None:
    with pytest.raises(ValueError, match="bit width"):
        TensorEdit(name="weight", expected_sha256="0" * 64, dtype="F32", shape=(1,),
                   replacements=(TensorReplacement(index=0, value_hex=value_hex),))


def test_empty_and_oversized_programs_rejected(contract: CheckpointContract, program: TensorEditProgram) -> None:
    with pytest.raises(ValueError):
        TensorEditProgram(input_digest=contract.digest, edits=())
    edit = program.edits[0]
    with pytest.raises(ValueError):
        TensorEdit(name=edit.name, expected_sha256=edit.expected_sha256, dtype=edit.dtype, shape=(MAX_REPLACEMENTS + 1,),
                   replacements=tuple(TensorReplacement(index=index, value_hex=bits(edit.dtype, 8.0).hex())
                                      for index in range(MAX_REPLACEMENTS + 1)))


@pytest.mark.parametrize("mutation", ["protected", "absent", "hash", "shape", "dtype", "noop", "input_digest"])
def test_invalid_program_never_creates_output(
    base: Path, contract: CheckpointContract, program: TensorEditProgram, tmp_path: Path, mutation: str,
) -> None:
    payload = program.to_dict()
    payload["edits"] = [payload["edits"][0]]
    edit = payload["edits"][0]
    if mutation == "protected":
        pin = contract.tensors[-1]
        edit.update(name=pin.name, expected_sha256=pin.sha256, shape=list(pin.shape), dtype=pin.dtype,
                    replacements=[{"index": 0, "value_hex": bits(pin.dtype, 5.0).hex()}])
    elif mutation == "absent":
        edit["name"] = "unknown.weight"
    elif mutation == "hash":
        edit["expected_sha256"] = "0" * 64
    elif mutation == "shape":
        edit["shape"] = [4]
    elif mutation == "dtype":
        edit["dtype"] = "F16"
    elif mutation == "noop":
        edit["replacements"][0]["value_hex"] = bits(edit["dtype"], 2.0).hex()
    else:
        payload["input_digest"] = "0" * 64
    changed = TensorEditProgram.model_validate_json(json.dumps(payload))
    with pytest.raises(ValueError):
        apply_tensor_edit(contract, changed, input_path=base, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_contract_changed_entry_budget_enforced(base: Path, contract: CheckpointContract,
                                               program: TensorEditProgram, tmp_path: Path) -> None:
    limited = contract.model_copy(update={"max_changed_entries": 1})
    changed = program.model_copy(update={"input_digest": limited.digest})
    with pytest.raises(ValueError, match="changed-entry budget"):
        apply_tensor_edit(limited, changed, input_path=base, output_dir=tmp_path / "out")


@pytest.mark.parametrize("field,value", [("sha256", "f" * 64), ("size_bytes", 12)])
def test_input_file_pin_mismatch_rejected(base: Path, contract: CheckpointContract, field: str, value: Any) -> None:
    changed = contract.model_copy(update={field: value})
    with pytest.raises(ValueError):
        verify_checkpoint_contract(changed, base)


@pytest.mark.parametrize("mutation", ["missing", "shape", "dtype", "hash"])
def test_complete_inventory_authenticated(base: Path, contract: CheckpointContract, mutation: str) -> None:
    data = contract.to_dict()
    if mutation == "missing":
        data["tensors"].pop()
    elif mutation == "shape":
        data["tensors"][0]["shape"] = [4]
    elif mutation == "dtype":
        data["tensors"][0]["dtype"] = "F16"
    else:
        data["tensors"][0]["sha256"] = "f" * 64
    changed = CheckpointContract.model_validate_json(json.dumps(data))
    with pytest.raises(ValueError):
        verify_checkpoint_contract(changed, base)


def test_output_tampering_fails_even_when_worker_receipts_are_rehashed(
    base: Path, contract: CheckpointContract, program: TensorEditProgram, tmp_path: Path,
) -> None:
    output = tmp_path / "out"
    receipt = apply_tensor_edit(contract, program, input_path=base, output_dir=output)
    path = output / "model.safetensors"
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1  # finite changed bits in the protected tensor
    path.write_bytes(raw)
    saved = receipt.to_dict()
    saved["output_sha256"] = hashlib.sha256(raw).hexdigest()
    (output / "receipt.json").write_text(json.dumps(saved))
    complete = json.loads((output / "complete.json").read_bytes())
    complete.update(output_sha256=saved["output_sha256"], receipt_digest=canonical_digest(saved))
    (output / "complete.json").write_text(json.dumps(complete))
    with pytest.raises(ValueError, match="independently reconstructed"):
        verify_tensor_edit(contract, program, input_path=base, output_dir=output)


@pytest.mark.parametrize("filename", ["receipt.json", "complete.json"])
def test_json_type_tampering_and_duplicate_keys_rejected(
    base: Path, contract: CheckpointContract, program: TensorEditProgram, tmp_path: Path, filename: str,
) -> None:
    output = tmp_path / "out"
    apply_tensor_edit(contract, program, input_path=base, output_dir=output)
    path = output / filename
    original = path.read_bytes()
    data = json.loads(original)
    data["schema_version"] = True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="independently verified"):
        verify_tensor_edit(contract, program, input_path=base, output_dir=output)
    path.write_bytes(b'{"schema_version":1,' + original[1:])
    with pytest.raises(ValueError, match="duplicate JSON key"):
        verify_tensor_edit(contract, program, input_path=base, output_dir=output)


@pytest.mark.parametrize("removed", ["receipt.json", "complete.json", "model.safetensors"])
def test_incomplete_output_is_never_repaired(base: Path, contract: CheckpointContract,
                                            program: TensorEditProgram, tmp_path: Path, removed: str) -> None:
    output = tmp_path / "out"
    apply_tensor_edit(contract, program, input_path=base, output_dir=output)
    (output / removed).unlink()
    with pytest.raises(ValueError, match="incomplete"):
        apply_tensor_edit(contract, program, input_path=base, output_dir=output)
    assert not (output / removed).exists()


def test_extra_output_file_rejected(base: Path, contract: CheckpointContract,
                                   program: TensorEditProgram, tmp_path: Path) -> None:
    output = tmp_path / "out"
    apply_tensor_edit(contract, program, input_path=base, output_dir=output)
    (output / "run.py").write_text("raise AssertionError('never execute')")
    with pytest.raises(ValueError, match="unexpected files"):
        verify_tensor_edit(contract, program, input_path=base, output_dir=output)


def test_symlink_input_output_and_receipt_rejected(base: Path, contract: CheckpointContract,
                                                  program: TensorEditProgram, tmp_path: Path) -> None:
    link = tmp_path / "input-link"
    link.symlink_to(base)
    with pytest.raises(ValueError, match="symlink"):
        verify_checkpoint_contract(contract, link)
    directory = tmp_path / "real"
    directory.mkdir()
    output = tmp_path / "output-link"
    output.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        apply_tensor_edit(contract, program, input_path=base, output_dir=output)
    actual = tmp_path / "actual"
    apply_tensor_edit(contract, program, input_path=base, output_dir=actual)
    (actual / "receipt.json").unlink()
    (actual / "receipt.json").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        verify_tensor_edit(contract, program, input_path=base, output_dir=actual)


def test_oversized_file_rejected_before_tensor_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.weight_evolution import tensor_edits

    path = tmp_path / "huge"
    with path.open("wb") as stream:
        stream.truncate(MAX_TINY_CHECKPOINT_BYTES + 1)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("oversized input reached tensor parser")

    monkeypatch.setattr(tensor_edits, "TensorFile", forbidden)
    with pytest.raises(ValueError, match="byte budget"):
        build_checkpoint_contract(path, editable_names=())


def test_special_input_file_rejected_without_blocking(tmp_path: Path) -> None:
    path = tmp_path / "fifo"
    os.mkfifo(path)
    with pytest.raises(ValueError, match="regular file"):
        build_checkpoint_contract(path, editable_names=())


def test_no_network_subprocess_or_gpu_runtime(base: Path, contract: CheckpointContract,
                                             program: TensorEditProgram, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("tensor edits attempted external execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    apply_tensor_edit(contract, program, input_path=base, output_dir=tmp_path / "out")


def test_public_entry_revalidates_model_copy_bypass(base: Path, contract: CheckpointContract,
                                                  program: TensorEditProgram, tmp_path: Path) -> None:
    forged = program.model_copy(update={"edits": ()})
    with pytest.raises(ValueError):
        apply_tensor_edit(contract, forged, input_path=base, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_scalar_tensor_contract_and_payload(tmp_path: Path) -> None:
    path = tmp_path / "scalar.safetensors"
    write_checkpoint(path, {"scalar": ("F32", (), bits("F32", 1.0))})
    contract = build_checkpoint_contract(path, editable_names=("scalar",))
    pin = contract.tensors[0]
    program = TensorEditProgram(input_digest=contract.digest, edits=(
        TensorEdit(name=pin.name, expected_sha256=pin.sha256, dtype=pin.dtype, shape=pin.shape,
                   replacements=(TensorReplacement(index=0, value_hex=bits("F32", 2.0).hex()),)),))
    receipt = apply_tensor_edit(contract, program, input_path=path, output_dir=tmp_path / "out")
    assert receipt.tensors[0].shape == () and receipt.changed_entries == 1


def test_oversized_and_unsorted_contracts_rejected(contract: CheckpointContract) -> None:
    with pytest.raises(ValueError, match="unique and sorted"):
        CheckpointContract(sha256=contract.sha256, size_bytes=contract.size_bytes, tensors=tuple(reversed(contract.tensors)))
    with pytest.raises(ValueError, match="byte cap"):
        TensorContract(name="huge", dtype="F64", shape=(MAX_TINY_CHECKPOINT_BYTES,), sha256="0" * 64)


def test_changed_implementation_identity_rejects_saved_receipt(
    base: Path, contract: CheckpointContract, program: TensorEditProgram, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.weight_evolution import tensor_edits

    output = tmp_path / "out"
    apply_tensor_edit(contract, program, input_path=base, output_dir=output)
    monkeypatch.setattr(tensor_edits, "tensor_edit_implementation_digest", lambda: "f" * 64)
    with pytest.raises(ValueError, match="independently verified"):
        apply_tensor_edit(contract, program, input_path=base, output_dir=output)


@pytest.mark.parametrize("mutation", ["shape", "dtype", "missing", "extra"])
def test_output_inventory_and_layout_rederived_from_input(
    base: Path, contract: CheckpointContract, program: TensorEditProgram, tmp_path: Path, mutation: str,
) -> None:
    output = tmp_path / "out"
    apply_tensor_edit(contract, program, input_path=base, output_dir=output)
    path = output / "model.safetensors"
    checkpoint = TensorFile(path)
    payloads = {name: (info.dtype, info.shape, checkpoint.read(name)) for name, info in checkpoint.tensors.items()}
    dtype, shape, data = payloads["edit.BF16"]
    if mutation == "shape":
        payloads["edit.BF16"] = (dtype, (4,), data)
    elif mutation == "dtype":
        payloads["edit.BF16"] = ("F16", shape, data)
    elif mutation == "missing":
        del payloads["frozen.weight"]
    else:
        payloads["unexpected.weight"] = ("F32", (1,), bits("F32", 7.0))
    path.unlink()
    write_checkpoint(path, payloads)
    with pytest.raises(ValueError, match="inventory|shape/dtype"):
        verify_tensor_edit(contract, program, input_path=base, output_dir=output)


def test_interrupted_publication_keeps_input_and_removes_unpublished_staging(
    base: Path, contract: CheckpointContract, program: TensorEditProgram, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.weight_evolution import tensor_edits

    original = base.read_bytes()
    output = tmp_path / "out"

    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("simulated storage failure")

    monkeypatch.setattr(tensor_edits, "_write_json", fail)
    with pytest.raises(OSError, match="simulated storage"):
        apply_tensor_edit(contract, program, input_path=base, output_dir=output)
    assert base.read_bytes() == original and not output.exists()
    assert not any(path.is_dir() and path.name.startswith(".out-") for path in tmp_path.iterdir())


def test_input_mutation_during_reconstruction_prevents_publication(
    base: Path, contract: CheckpointContract, program: TensorEditProgram, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.weight_evolution import tensor_edits

    write = tensor_edits.write_tensor_file

    def mutate(*args: Any, **kwargs: Any) -> None:
        write(*args, **kwargs)
        base.write_bytes(base.read_bytes() + b"tampered")

    monkeypatch.setattr(tensor_edits, "write_tensor_file", mutate)
    with pytest.raises(ValueError, match="pinned size/hash"):
        apply_tensor_edit(contract, program, input_path=base, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()
