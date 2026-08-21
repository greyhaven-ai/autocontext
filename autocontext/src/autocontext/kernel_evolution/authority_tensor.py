"""Canonical safetensors payloads for accelerator authority messages."""

from __future__ import annotations

from typing import Any

from autocontext.kernel_evolution.authority_protocol import canonical_authority_digest
from autocontext.kernel_evolution.authority_wire import AuthorityWireError


def _tensor_manifest(tensors: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {
            "dtype": str(tensor.dtype),
            "shape": list(tensor.shape),
            "stride": list(tensor.stride()),
            "bytes": tensor.numel() * tensor.element_size(),
        }
        for name, tensor in sorted(tensors.items())
    }


def serialize_tensor_list(values: list[Any], *, prefix: str) -> tuple[bytes, str]:
    """Serialize positional tensors without pickle or executable metadata."""

    from safetensors.torch import save
    from torch import Tensor

    if any(not isinstance(value, Tensor) for value in values):
        raise TypeError("authority v1 accepts only positional tensor values")
    tensors = {
        f"{prefix}_{index:04d}": value.detach().cpu().contiguous()
        for index, value in enumerate(values)
    }
    payload = save(tensors) if tensors else b""
    return payload, canonical_authority_digest(_tensor_manifest(tensors))


def deserialize_tensor_list(payload: bytes, *, prefix: str) -> tuple[list[Any], str]:
    """Decode a bounded safetensors payload with canonical positional names."""

    from safetensors.torch import load

    if not payload:
        return [], canonical_authority_digest({})
    tensors = load(payload)
    expected = [f"{prefix}_{index:04d}" for index in range(len(tensors))]
    if sorted(tensors) != expected:
        raise AuthorityWireError("authority tensor payload contains non-canonical names")
    return [tensors[name] for name in expected], canonical_authority_digest(_tensor_manifest(tensors))


__all__ = ["deserialize_tensor_list", "serialize_tensor_list"]
