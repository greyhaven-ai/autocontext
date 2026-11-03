"""Trusted, deterministic token-sum inference over verified F32 checkpoint bytes.

The forward pass and sampler use only the Python standard library. M2 provides
the safetensors index and safe file access; F32 decoding uses struct rather than
TensorFile.read, whose finite-value helper imports NumPy. In-process inference
has structural bounds only; the separate worker owns hard resource enforcement.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path

from autocontext.weight_evolution.artifacts import (
    CheckpointError,
    TensorFile,
    _unique_object,
    canonical_digest,
    file_identity,
    regular_file,
)
from autocontext.weight_evolution.cpu_models import (
    MAX_CPU_CHECKPOINT_BYTES,
    MAX_CPU_OUTPUT_TOKENS,
    CpuDecoding,
    CpuInferenceRequest,
    CpuInferenceResult,
    CpuModelContract,
)
from autocontext.weight_evolution.tensor_edits import CheckpointContract, TensorContract

_WEIGHT = "projection.weight"
_BIAS = "projection.bias"


def _verified_parameters(
    model: CpuModelContract, checkpoint_path: Path, *, expected_sha256: str, expected_size_bytes: int,
) -> tuple[CheckpointContract, tuple[float, ...], tuple[float, ...]]:
    model = CpuModelContract.model_validate_json(json.dumps(model.to_dict(), allow_nan=False))
    # Validate standalone callers' pins before any filesystem access.
    contract = CheckpointContract(
        sha256=expected_sha256, size_bytes=expected_size_bytes,
        tensors=(TensorContract(name=_BIAS, dtype="F32", shape=(len(model.output_tokens),), sha256="0" * 64),
                 TensorContract(name=_WEIGHT, dtype="F32", shape=(len(model.vocabulary), len(model.output_tokens)),
                                sha256="0" * 64)),
    )
    with regular_file(checkpoint_path) as stream:
        raw = stream.read(MAX_CPU_CHECKPOINT_BYTES + 1)
    if len(raw) != contract.size_bytes or hashlib.sha256(raw).hexdigest() != contract.sha256:
        raise CheckpointError("CPU checkpoint differs from its pinned size/hash")

    indexed = TensorFile(checkpoint_path)
    if set(indexed.tensors) != {_BIAS, _WEIGHT}:
        raise CheckpointError("CPU checkpoint requires exactly projection.bias and projection.weight")
    # The parser opens the path separately. Match its complete tensor index to
    # the pinned immutable byte snapshot so a replacement cannot supply a second
    # file's offsets while inference reads bytes from the originally pinned file.
    header_length = struct.unpack("<Q", raw[:8])[0]
    if indexed.data_start != 8 + header_length or not raw[8:9] == b"{":
        raise CheckpointError("CPU checkpoint header changed during verification")
    header = json.loads(raw[8:indexed.data_start], object_pairs_hook=_unique_object)
    if not isinstance(header, dict) or set(header) - {"__metadata__"} != set(indexed.tensors):
        raise CheckpointError("CPU checkpoint snapshot differs from its verified tensor index")
    if "__metadata__" in header and (not isinstance(header["__metadata__"], dict)
                                    or any(not isinstance(value, str) for value in header["__metadata__"].values())):
        raise CheckpointError("invalid CPU checkpoint metadata")
    payloads = {}
    pins = []
    for pin in contract.tensors:
        info = indexed.tensors[pin.name]
        expected_header = {"dtype": info.dtype, "shape": list(info.shape), "data_offsets": [info.start, info.end]}
        if canonical_digest(header[pin.name]) != canonical_digest(expected_header):
            raise CheckpointError("CPU checkpoint header changed during verification")
        if (info.dtype, info.shape) != (pin.dtype, pin.shape):
            raise CheckpointError("CPU tensor shape/dtype differs from the model contract")
        payload = raw[indexed.data_start + info.start:indexed.data_start + info.end]
        if len(payload) != info.end - info.start:
            raise CheckpointError("CPU checkpoint tensor payload is truncated")
        values = tuple(value[0] for value in struct.iter_unpack("<f", payload))
        if any(not math.isfinite(value) for value in values):
            raise CheckpointError("nonfinite CPU checkpoint tensor value")
        payloads[pin.name] = values
        pins.append(TensorContract(name=pin.name, dtype=pin.dtype, shape=pin.shape,
                                   sha256=hashlib.sha256(payload).hexdigest()))
    identity = file_identity(checkpoint_path, max_bytes=MAX_CPU_CHECKPOINT_BYTES)
    if identity["size_bytes"] != contract.size_bytes or identity["sha256"] != contract.sha256:
        raise CheckpointError("CPU checkpoint changed during verification")
    verified = CheckpointContract(sha256=contract.sha256, size_bytes=contract.size_bytes, tensors=tuple(pins))
    return verified, payloads[_WEIGHT], payloads[_BIAS]


def verify_model_checkpoint(
    model: CpuModelContract, checkpoint_path: Path, *, expected_sha256: str, expected_size_bytes: int,
) -> CheckpointContract:
    """Read-only full-file, inventory, shape, dtype, finite-value and tensor pins."""
    return _verified_parameters(model, checkpoint_path, expected_sha256=expected_sha256,
                                expected_size_bytes=expected_size_bytes)[0]


def _sample_uniform(seed: int) -> float:
    """SHA256 domain+ASCII seed, first 53 big-endian bits divided by 2**53.

    The draw ignores request/checkpoint/context identities, preserving common
    randomness for paired conditions. Callers may pin a separate seed per case.
    """
    digest = hashlib.sha256(f"autocontext-cpu-sample-v1:{seed}".encode("ascii")).digest()
    return (int.from_bytes(digest[:8], "big") >> 11) / (1 << 53)


def decode_cpu_logits(logits: tuple[float, ...], decoding: CpuDecoding) -> int:
    """Validate and decode saved logits without checkpoint access or inference."""
    decoding = CpuDecoding.model_validate_json(json.dumps(decoding.to_dict(), allow_nan=False))
    if (type(logits) is not tuple or not 2 <= len(logits) <= MAX_CPU_OUTPUT_TOKENS
            or any(type(value) is not float or not math.isfinite(value) for value in logits)):
        raise ValueError("CPU decoding requires a bounded tuple of finite float logits")
    if decoding.top_k > len(logits):
        raise ValueError("top_k exceeds the CPU logit inventory")
    ranked = sorted(range(len(logits)), key=lambda index: (-logits[index], index))
    if decoding.mode == "greedy":
        return ranked[0]
    retained = ranked[:decoding.top_k] if decoding.top_k else ranked
    maximum = logits[retained[0]]
    masses = tuple(math.exp((logits[index] - maximum) / decoding.temperature) for index in retained)
    threshold = _sample_uniform(decoding.seed) * math.fsum(masses)
    for position, index in enumerate(retained):
        if threshold < math.fsum(masses[:position + 1]):
            return index
    return retained[-1]


def infer_cpu(request: CpuInferenceRequest, checkpoint_path: Path) -> CpuInferenceResult:
    """One stdlib CPU forward pass and one deterministic decoded output token.

    Input tokens are Python whitespace splits with exact vocabulary matching.
    F32 payloads are exactly widened to Python binary64; each output accumulates
    bias, then token contributions left-to-right in prompt order. Greedy ties
    choose the lowest output ID. Sampling sorts by descending logit/ascending
    ID, applies top-k (0 means all), and computes max-shifted softmax weights at
    the requested temperature. CPU platform/runtime identity belongs in the
    worker receipt; this function does not claim cross-platform attestation.
    """
    request = CpuInferenceRequest.model_validate_json(json.dumps(request.to_dict(), allow_nan=False))
    contract, weight, bias = _verified_parameters(
        request.model, checkpoint_path, expected_sha256=request.checkpoint_sha256,
        expected_size_bytes=request.checkpoint_size_bytes,
    )
    token_ids = {token: index for index, token in enumerate(request.model.vocabulary)}
    inputs = tuple(token_ids[token] for token in request.prompt.split())
    output_count = len(request.model.output_tokens)
    values = list(bias)
    for token_id in inputs:
        for output_id in range(output_count):
            values[output_id] += weight[token_id * output_count + output_id]
    logits = tuple(values)
    selected = decode_cpu_logits(logits, request.decoding)
    return CpuInferenceResult(
        request_digest=request.digest, checkpoint_sha256=contract.sha256,
        checkpoint_size_bytes=contract.size_bytes, input_tokens=inputs, logits=logits,
        output_token_id=selected, text=request.model.output_tokens[selected],
    )
