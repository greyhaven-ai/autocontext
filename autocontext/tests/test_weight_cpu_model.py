from __future__ import annotations

import builtins
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

from autocontext.weight_evolution.artifacts import TensorFile, file_identity
from autocontext.weight_evolution.cpu_model import _sample_uniform, decode_cpu_logits, infer_cpu, verify_model_checkpoint
from autocontext.weight_evolution.cpu_models import (
    MAX_CPU_CHECKPOINT_BYTES,
    CpuDecoding,
    CpuInferenceRequest,
    CpuInferenceResult,
    CpuModelContract,
    CpuWorkerLimits,
)
from autocontext.weight_evolution.tensor_edits import (
    TensorEdit,
    TensorEditProgram,
    TensorReplacement,
    apply_tensor_edit,
    build_checkpoint_contract,
)


def write_checkpoint(path: Path, *, weight: tuple[float, ...] = (0.0, 1.0, 0.0, 3.0, 2.0, 0.0, 0.0, 0.0),
                     bias: tuple[float, ...] = (0.0, 0.0), reverse: bool = False) -> None:
    records = [("projection.bias", (2,), bias), ("projection.weight", (4, 2), weight)]
    if reverse:
        records.reverse()
    header: dict[str, Any] = {"__metadata__": {"format": "pt"}}
    body = b""
    for name, shape, values in records:
        payload = struct.pack(f"<{len(values)}f", *values)
        header[name] = {"dtype": "F32", "shape": list(shape), "data_offsets": [len(body), len(body) + len(payload)]}
        body += payload
    encoded = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    encoded += b" " * (-len(encoded) % 8)
    path.write_bytes(struct.pack("<Q", len(encoded)) + encoded + body)


@pytest.fixture
def model() -> CpuModelContract:
    return CpuModelContract(vocabulary=("qpos", "qneg", "hint", "neutral"), output_tokens=("positive", "negative"))


@pytest.fixture
def base(tmp_path: Path) -> Path:
    path = tmp_path / "base.safetensors"
    write_checkpoint(path)
    return path


def request(model: CpuModelContract, checkpoint: Path, prompt: str = "qpos", *,
            decoding: CpuDecoding | None = None, limits: CpuWorkerLimits | None = None) -> CpuInferenceRequest:
    raw = checkpoint.read_bytes()
    return CpuInferenceRequest(request_id="case", model=model, checkpoint_sha256=hashlib.sha256(raw).hexdigest(),
                               checkpoint_size_bytes=len(raw), prompt=prompt,
                               decoding=decoding or CpuDecoding(), limits=limits or CpuWorkerLimits())


def verify(model: CpuModelContract, path: Path) -> Any:
    identity = file_identity(path)
    return verify_model_checkpoint(model, path, expected_sha256=identity["sha256"], expected_size_bytes=identity["size_bytes"])


def test_forward_pass_uses_pinned_bytes_and_exports_only_cpu_evidence(model: CpuModelContract, base: Path) -> None:
    original = base.read_bytes()
    query = request(model, base)
    result = infer_cpu(query, base)
    assert result.logits == (0.0, 1.0)
    assert result.input_tokens == (0,)
    assert result.output_token_id == 1 and result.text == "negative"
    assert result.request_digest == query.digest
    assert result.checkpoint_sha256 == query.checkpoint_sha256
    assert result.checkpoint_size_bytes == len(original)
    assert result.execution_kind == "cpu_inference" and result.device == "cpu"
    assert result.model_execution_performed and result.gpu_execution_deferred and not result.promotion_eligible
    assert base.read_bytes() == original
    contract = verify(model, base)
    assert contract.sha256 == result.checkpoint_sha256
    assert len(contract.tensors) == 2 and all(not pin.editable for pin in contract.tensors)


def test_context_and_repeated_tokens_affect_logits(model: CpuModelContract, base: Path) -> None:
    original = infer_cpu(request(model, base), base)
    contextual = infer_cpu(request(model, base, "qpos hint"), base)
    assert contextual.logits == (2.0, 1.0) and contextual.text == "positive"
    assert infer_cpu(request(model, base, "qneg hint"), base).text == "negative"
    assert infer_cpu(request(model, base, "qpos qpos hint"), base).logits == (2.0, 2.0)
    whitespace = infer_cpu(request(model, base, "\tqpos\nneutral  neutral\t"), base)
    assert whitespace.input_tokens == (0, 3, 3) and whitespace.logits == original.logits
    assert whitespace.text == original.text


def test_real_m8_edits_change_behavior_and_restoring_checkpoint_restores_outputs(
    model: CpuModelContract, base: Path, tmp_path: Path,
) -> None:
    contract = build_checkpoint_contract(base, editable_names=("projection.weight",), max_changed_entries=1)
    pin = next(pin for pin in contract.tensors if pin.editable)
    original = base.read_bytes()
    paths = {}
    for label, index, replacement in (("helpful", 0, 2.0), ("harmful", 2, 4.0)):
        program = TensorEditProgram(input_digest=contract.digest, edits=(
            TensorEdit(name=pin.name, expected_sha256=pin.sha256, dtype=pin.dtype, shape=pin.shape,
                       replacements=(TensorReplacement(index=index, value_hex=struct.pack("<f", replacement).hex()),)),))
        apply_tensor_edit(contract, program, input_path=base, output_dir=tmp_path / label)
        paths[label] = tmp_path / label / "model.safetensors"
    baseline = tuple(infer_cpu(request(model, base, prompt), base) for prompt in ("qpos", "qneg"))
    helpful = tuple(infer_cpu(request(model, paths["helpful"], prompt), paths["helpful"]).text
                    for prompt in ("qpos", "qneg"))
    harmful = tuple(infer_cpu(request(model, paths["harmful"], prompt), paths["harmful"]).text
                    for prompt in ("qpos", "qneg"))
    assert tuple(result.text for result in baseline) == ("negative", "negative")
    assert helpful == ("positive", "negative")
    assert harmful == ("negative", "positive")
    assert tuple(infer_cpu(request(model, base, prompt), base) for prompt in ("qpos", "qneg")) == baseline
    assert base.read_bytes() == original


def test_sampling_temperature_top_k_and_seed_are_effective(model: CpuModelContract, base: Path) -> None:
    def decode(**settings: Any) -> CpuInferenceResult:
        return infer_cpu(request(model, base, decoding=CpuDecoding(mode="sample", **settings)), base)

    assert decode(temperature=2.0, top_k=2, seed=0).text == "positive"
    assert decode(temperature=2.0, top_k=0, seed=0).text == "positive"
    assert decode(temperature=0.1, top_k=2, seed=0).text == "negative"
    assert decode(temperature=2.0, top_k=1, seed=0).text == "negative"
    assert decode(temperature=2.0, top_k=2, seed=2).text == "negative"
    repeated = decode(temperature=2.0, top_k=2, seed=0)
    assert repeated == decode(temperature=2.0, top_k=2, seed=0)


def test_sampler_has_pinned_hash_vectors_and_common_randomness(model: CpuModelContract, base: Path) -> None:
    assert _sample_uniform(0) == 0.6900259054075418
    assert _sample_uniform(2) == 0.3096210619138068
    assert _sample_uniform(406) == 0.9669424138871723
    query = request(model, base, decoding=CpuDecoding(mode="sample", temperature=2.0))
    changed = query.model_copy(update={"request_id": "another-case"})
    a, b = infer_cpu(query, base), infer_cpu(changed, base)
    assert a.request_digest != b.request_digest
    assert (a.logits, a.output_token_id, a.text) == (b.logits, b.output_token_id, b.text)


def test_saved_logits_decode_without_checkpoint_access_or_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("saved-logit decoding attempted checkpoint access or a forward pass")

    monkeypatch.setattr("autocontext.weight_evolution.cpu_model._verified_parameters", forbidden)
    monkeypatch.setattr("autocontext.weight_evolution.cpu_model.infer_cpu", forbidden)
    assert decode_cpu_logits((0.0, 1.0), CpuDecoding()) == 1
    assert decode_cpu_logits((0.0, 1.0), CpuDecoding(mode="sample", temperature=2.0, top_k=2, seed=0)) == 0


@pytest.mark.parametrize("logits", [(), (0.0,), (0.0,) * 9, (math.inf, 0.0), (math.nan, 0.0), (True, 0.0), [0.0, 1.0]])
def test_saved_logit_decoder_rejects_invalid_logit_evidence(logits: Any) -> None:
    with pytest.raises(ValueError, match="finite float logits"):
        decode_cpu_logits(logits, CpuDecoding())


def test_saved_logit_decoder_revalidates_decoding_contract() -> None:
    with pytest.raises(ValueError, match="top_k"):
        decode_cpu_logits((0.0, 1.0), CpuDecoding(top_k=3))
    with pytest.raises(ValueError):
        decode_cpu_logits((0.0, 1.0), CpuDecoding().model_copy(update={"temperature": math.nan}))


def test_ties_choose_lowest_id_and_softmax_handles_large_finite_values(
    model: CpuModelContract, base: Path,
) -> None:
    write_checkpoint(base, weight=(0.0,) * 8, bias=(1.0, 1.0))
    assert infer_cpu(request(model, base), base).output_token_id == 0
    assert infer_cpu(request(model, base, decoding=CpuDecoding(mode="sample", top_k=1)), base).output_token_id == 0
    write_checkpoint(base, weight=(3.0e38, -3.0e38, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    result = infer_cpu(request(model, base, " ".join(["qpos"] * 64),
                               decoding=CpuDecoding(mode="sample", temperature=1e-300)), base)
    assert result.output_token_id == 0 and all(math.isfinite(logit) for logit in result.logits)


@pytest.mark.parametrize("field,value", [
    ("vocabulary", ()), ("vocabulary", ("x", "x")), ("vocabulary", ("two tokens",)),
    ("vocabulary", ("x\t",)), ("vocabulary", ("x\x00",)), ("vocabulary", ("x\u200b",)),
    ("vocabulary", tuple(f"token{n}" for n in range(33))), ("output_tokens", ("one",)),
    ("output_tokens", ("one", "one")), ("output_tokens", tuple(str(n) for n in range(9))),
    ("output_tokens", ("yes", "no thanks")), ("architecture", "downloaded-transformer"),
    ("schema_version", True),
])
def test_model_contract_rejects_ambiguous_or_unbounded_inputs(model: CpuModelContract, field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        CpuModelContract.model_validate_json(json.dumps({**model.to_dict(), field: value}))


@pytest.mark.parametrize("field,value", [
    ("memory_mb", 255), ("memory_mb", 2049), ("memory_mb", True),
    ("wall_seconds", 0.01), ("wall_seconds", 30.1), ("wall_seconds", math.nan), ("wall_seconds", math.inf),
    ("max_input_tokens", 0), ("max_input_tokens", 65), ("max_input_tokens", True),
    ("max_output_tokens", 2), ("max_output_tokens", True),
])
def test_worker_limit_contract_is_strict_and_bounded(field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        CpuWorkerLimits.model_validate_json(json.dumps({field: value}))


@pytest.mark.parametrize("field,value", [
    ("temperature", 0.0), ("temperature", -1.0), ("temperature", math.inf), ("temperature", math.nan),
    ("temperature", 100.1), ("top_k", -1), ("top_k", 9), ("top_k", True),
    ("seed", -1), ("seed", 2**32), ("seed", True), ("mode", "cuda"),
])
def test_decoding_is_strict_finite_and_bounded(field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        CpuDecoding.model_validate_json(json.dumps({field: value}))


@pytest.mark.parametrize("prompt", ["", " \t\n", "unknown", "qpos unknown", " ".join(["qpos"] * 65)])
def test_bad_prompts_are_rejected_before_inference(model: CpuModelContract, base: Path, prompt: str) -> None:
    with pytest.raises(ValueError):
        request(model, base, prompt)


def test_smaller_token_limit_and_multibyte_prompt_budget_enforced(model: CpuModelContract, base: Path) -> None:
    with pytest.raises(ValueError, match="token bound"):
        request(model, base, "qpos qpos", limits=CpuWorkerLimits(max_input_tokens=1))
    token = "😀" * 64
    expanded = CpuModelContract(vocabulary=(token,), output_tokens=model.output_tokens)
    with pytest.raises(ValueError, match="UTF-8 byte cap"):
        request(expanded, base, " ".join([token] * 33))
    with pytest.raises(ValueError, match="top_k"):
        request(model, base, decoding=CpuDecoding(top_k=3))


@pytest.mark.parametrize("field", ["gold", "expected_answer", "response_catalog", "scorer", "device", "checkpoint_path"])
def test_requests_have_no_gold_scorer_device_or_path_authority(model: CpuModelContract, base: Path, field: str) -> None:
    data = request(model, base).to_dict()
    with pytest.raises(ValueError):
        CpuInferenceRequest.model_validate_json(json.dumps({**data, field: "untrusted"}))


@pytest.mark.parametrize("field,value", [
    ("execution_kind", "fixture"), ("device", "cuda"), ("model_execution_performed", False),
    ("gpu_execution_deferred", False), ("promotion_eligible", True), ("model_execution_performed", 1),
    ("gpu_execution_deferred", 1), ("promotion_eligible", 0), ("schema_version", True),
    ("logits", [math.inf, 0.0]), ("logits", [0.0]), ("input_tokens", [True]),
    ("input_tokens", [32]), ("input_tokens", []), ("output_token_id", 2), ("text", "two words"),
])
def test_results_cannot_claim_other_execution_or_invalid_evidence(
    model: CpuModelContract, base: Path, field: str, value: Any,
) -> None:
    payload = infer_cpu(request(model, base), base).to_dict()
    with pytest.raises(ValueError):
        CpuInferenceResult.model_validate_json(json.dumps({**payload, field: value}))


@pytest.mark.parametrize("damage", ["missing", "extra", "shape", "dtype", "duplicate"])
def test_exact_two_tensor_inventory_and_layout_are_required(model: CpuModelContract, base: Path, damage: str) -> None:
    raw = base.read_bytes()
    length = struct.unpack("<Q", raw[:8])[0]
    header = json.loads(raw[8:8 + length])
    if damage == "missing":
        header.pop("projection.bias")
    elif damage == "extra":
        header["unexpected"] = header.pop("projection.bias")
    elif damage == "shape":
        header["projection.weight"]["shape"] = [2, 4]
    elif damage == "dtype":
        header["projection.weight"].update(dtype="F16", shape=[4, 4])
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    if damage == "duplicate":
        encoded = b'{"projection.bias":{},' + encoded[1:]
    encoded += b" " * (-len(encoded) % 8)
    base.write_bytes(struct.pack("<Q", len(encoded)) + encoded + raw[8 + length:])
    with pytest.raises(ValueError):
        verify(model, base)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_nonfinite_weights_are_rejected_even_with_matching_file_pin(model: CpuModelContract, base: Path, value: float) -> None:
    write_checkpoint(base, weight=(value, 1.0, 0.0, 3.0, 2.0, 0.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="nonfinite"):
        infer_cpu(request(model, base), base)


@pytest.mark.parametrize("field,value", [("checkpoint_sha256", "0" * 64), ("checkpoint_size_bytes", 10)])
def test_wrong_input_pins_rejected(model: CpuModelContract, base: Path, field: str, value: Any) -> None:
    forged = request(model, base).model_copy(update={field: value})
    with pytest.raises(ValueError):
        infer_cpu(forged, base)


def test_model_copy_bypass_is_revalidated(model: CpuModelContract, base: Path) -> None:
    query = request(model, base)
    changed = query.model_copy(update={"limits": query.limits.model_copy(update={"max_input_tokens": 1000000})})
    with pytest.raises(ValueError):
        infer_cpu(changed, base)
    changed_model = model.model_copy(update={"vocabulary": ("x", "x")})
    with pytest.raises(ValueError):
        verify(changed_model, base)


def test_symlink_and_special_files_fail_without_external_reads(model: CpuModelContract, base: Path, tmp_path: Path) -> None:
    query = request(model, base)
    link = tmp_path / "alias"
    link.symlink_to(base)
    with pytest.raises(ValueError, match="symlink"):
        infer_cpu(query, link)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular file"):
        infer_cpu(query, fifo)


def test_oversized_input_stops_before_parser(model: CpuModelContract, base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    query = request(model, base)
    with base.open("wb") as stream:
        stream.truncate(MAX_CPU_CHECKPOINT_BYTES + 1)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("oversized input reached safetensors parser")

    monkeypatch.setattr("autocontext.weight_evolution.cpu_model.TensorFile", forbidden)
    with pytest.raises(ValueError, match="size/hash"):
        infer_cpu(query, base)


def test_parser_index_cannot_be_substituted_between_pinned_reads(
    model: CpuModelContract, base: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = request(model, base)
    original = base.read_bytes()

    def substituted(path: Path) -> TensorFile:
        write_checkpoint(path, reverse=True)
        indexed = TensorFile(path)
        path.write_bytes(original)
        return indexed

    monkeypatch.setattr("autocontext.weight_evolution.cpu_model.TensorFile", substituted)
    with pytest.raises(ValueError, match="header changed"):
        infer_cpu(query, base)


def test_mutation_during_verification_fails_closed(model: CpuModelContract, base: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    query = request(model, base)

    def changed(path: Path, **kwargs: Any) -> Any:
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
        return file_identity(path, **kwargs)

    monkeypatch.setattr("autocontext.weight_evolution.cpu_model.file_identity", changed)
    with pytest.raises(ValueError, match="changed during verification"):
        infer_cpu(query, base)


def test_cpu_execution_uses_no_numpy_gpu_network_or_subprocess(
    model: CpuModelContract, base: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = request(model, base)
    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".", 1)[0] in {"numpy", "torch", "transformers", "vllm", "cupy", "jax"}:
            raise AssertionError("CPU forward pass imported a model/GPU/array runtime")
        return original_import(name, *args, **kwargs)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("CPU forward pass attempted external execution")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(TensorFile, "read", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert infer_cpu(query, base).text == "negative"
