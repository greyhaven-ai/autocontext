"""Canonical safetensors payloads for accelerator authority messages."""

from __future__ import annotations

import json
import re
import struct
from collections.abc import Mapping, Sequence
from typing import Any

from autocontext.kernel_evolution.authority_protocol import canonical_authority_digest
from autocontext.kernel_evolution.authority_wire import MAX_AUTHORITY_PAYLOAD_BYTES, AuthorityWireError

_TENSOR_MAGIC = b"ACTENS2\0"
_PREFIX = struct.Struct("!8sI")
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_TENSOR_COUNT = 4096
_MAX_EXACT_LAYOUT_ELEMENTS = 65_536
_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_MANIFEST_FIELDS = {"bytes", "dtype", "shape", "storage_offset", "stride"}


def _tensor_manifest(tensors: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: {
            "dtype": str(tensor.dtype),
            "shape": list(tensor.shape),
            "stride": list(tensor.stride()),
            "storage_offset": tensor.storage_offset(),
            "bytes": tensor.numel() * tensor.element_size(),
        }
        for name, tensor in sorted(tensors.items())
    }


def _checked_layout(
    shape: Sequence[int],
    stride: Sequence[int],
    storage_offset: int,
    element_size: int,
) -> int:
    """Return the required backing-storage length for a supported layout."""

    if len(shape) != len(stride):
        raise AuthorityWireError("authority tensor shape and stride ranks differ")
    if storage_offset < 0 or element_size < 1:
        raise AuthorityWireError("authority tensor has an invalid storage layout")
    if any(dimension < 0 for dimension in shape) or any(step < 0 for step in stride):
        raise AuthorityWireError("authority tensor uses an unsupported negative shape or stride")

    logical_elements = 1
    for dimension in shape:
        logical_elements *= dimension
    if logical_elements == 0:
        storage_elements = storage_offset
    else:
        required_span = 1
        needs_exact_check = False
        for step, dimension in sorted(
            (step, dimension) for step, dimension in zip(stride, shape, strict=True) if dimension > 1
        ):
            if step < required_span:
                needs_exact_check = True
            required_span += (dimension - 1) * step
        storage_elements = storage_offset + required_span
        if needs_exact_check:
            if logical_elements > _MAX_EXACT_LAYOUT_ELEMENTS:
                raise AuthorityWireError("authority tensor uses an unsupported irregular storage layout")
            _reject_exact_layout_overlap(shape, stride)

    if storage_elements * element_size > MAX_AUTHORITY_PAYLOAD_BYTES:
        raise AuthorityWireError("authority tensor backing storage exceeds the wire payload limit")
    return storage_elements


def _reject_exact_layout_overlap(shape: Sequence[int], stride: Sequence[int]) -> None:
    """Reject collisions by enumerating a fixed-bounded irregular layout."""

    offsets = {0}
    for dimension, step in zip(shape, stride, strict=True):
        if dimension <= 1:
            continue
        expanded: set[int] = set()
        for index in range(dimension):
            delta = index * step
            for offset in offsets:
                expanded_offset = offset + delta
                if expanded_offset in expanded:
                    raise AuthorityWireError("authority tensor uses unsupported overlapping storage")
                expanded.add(expanded_offset)
        offsets = expanded


def _storage_identity(tensor: Any) -> tuple[str, int] | None:
    try:
        storage = tensor.untyped_storage()
    except (RuntimeError, TypeError) as exc:
        raise AuthorityWireError("authority tensor does not expose supported backing storage") from exc
    if storage.nbytes() == 0:
        return None
    return str(tensor.device), int(storage.data_ptr())


def _validate_source_tensors(values: Sequence[Any]) -> None:
    from torch import Tensor, strided

    if len(values) > _MAX_TENSOR_COUNT:
        raise AuthorityWireError("authority tensor payload contains too many tensors")
    if any(not isinstance(value, Tensor) for value in values):
        raise TypeError("authority v1 accepts only positional tensor values")

    storage_identities: set[tuple[str, int]] = set()
    for value in values:
        if value.layout != strided or value.is_quantized or value.device.type == "meta":
            raise AuthorityWireError("authority tensor uses an unsupported layout or device")
        shape = tuple(int(dimension) for dimension in value.shape)
        stride = tuple(int(step) for step in value.stride())
        _checked_layout(shape, stride, int(value.storage_offset()), int(value.element_size()))
        identity = _storage_identity(value)
        if identity is not None and identity in storage_identities:
            raise AuthorityWireError("authority tensor list contains unsupported storage aliases")
        if identity is not None:
            storage_identities.add(identity)


def _canonical_tensor_names(prefix: str, count: int) -> list[str]:
    if _SAFE_NAME.fullmatch(prefix) is None:
        raise ValueError("authority tensor prefix is not a safe canonical name")
    return [f"{prefix}_{index:04d}" for index in range(count)]


def _encode_metadata(magic: bytes, metadata: dict[str, Any], body: bytes) -> bytes:
    encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > _MAX_METADATA_BYTES:
        raise AuthorityWireError("authority tensor metadata exceeded its fixed byte limit")
    payload = _PREFIX.pack(magic, len(encoded)) + encoded + body
    if len(payload) > MAX_AUTHORITY_PAYLOAD_BYTES:
        raise AuthorityWireError("authority tensor payload exceeded the wire payload limit")
    return payload


def _decode_metadata(payload: bytes, magic: bytes) -> tuple[dict[str, Any], bytes]:
    if len(payload) < _PREFIX.size:
        raise AuthorityWireError("authority tensor payload ended before its metadata prefix")
    decoded_magic, metadata_size = _PREFIX.unpack(payload[: _PREFIX.size])
    if decoded_magic != magic:
        raise AuthorityWireError("authority tensor payload used an unknown format")
    if metadata_size < 2 or metadata_size > _MAX_METADATA_BYTES:
        raise AuthorityWireError("authority tensor payload declared invalid metadata")
    body_offset = _PREFIX.size + metadata_size
    if body_offset > len(payload):
        raise AuthorityWireError("authority tensor payload ended before its declared metadata")
    try:
        metadata = json.loads(
            payload[_PREFIX.size : body_offset].decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise AuthorityWireError("authority tensor payload contained invalid metadata") from exc
    if not isinstance(metadata, dict):
        raise AuthorityWireError("authority tensor metadata must be an object")
    return metadata, payload[body_offset:]


def serialize_tensor_list(values: list[Any], *, prefix: str) -> tuple[bytes, str]:
    """Serialize positional tensors while retaining their exact strided ABI."""

    if len(values) > _MAX_TENSOR_COUNT:
        raise AuthorityWireError("authority tensor payload contains too many tensors")
    names = _canonical_tensor_names(prefix, len(values))
    if not names:
        return b"", canonical_authority_digest({})
    import torch
    from safetensors.torch import save

    _validate_source_tensors(values)
    storages: dict[str, Any] = {}
    views: dict[str, Any] = {}
    planned_bytes = 0
    for name, value in zip(names, values, strict=True):
        shape = tuple(int(dimension) for dimension in value.shape)
        stride = tuple(int(step) for step in value.stride())
        storage_offset = int(value.storage_offset())
        storage_elements = _checked_layout(shape, stride, storage_offset, int(value.element_size()))
        planned_bytes += storage_elements * int(value.element_size())
        if planned_bytes > MAX_AUTHORITY_PAYLOAD_BYTES:
            raise AuthorityWireError("authority tensor backing storage exceeds the wire payload limit")
        try:
            storage = torch.zeros(storage_elements, dtype=value.dtype, device="cpu")
            view = torch.as_strided(storage, shape, stride, storage_offset)
            view.copy_(value.detach().resolve_conj().resolve_neg().cpu())
        except (RuntimeError, TypeError) as exc:
            raise AuthorityWireError("authority tensor could not preserve its declared storage layout") from exc
        storages[name] = storage
        views[name] = view

    manifest = _tensor_manifest(views)
    try:
        safetensors_payload = save(storages)
    except Exception as exc:
        raise AuthorityWireError("authority tensor dtype is not supported by safetensors") from exc
    payload = _encode_metadata(_TENSOR_MAGIC, manifest, safetensors_payload)
    return payload, canonical_authority_digest(manifest)


def _validate_manifest(metadata: dict[str, Any], prefix: str) -> list[str]:
    if len(metadata) > _MAX_TENSOR_COUNT:
        raise AuthorityWireError("authority tensor payload contains too many tensors")
    names = _canonical_tensor_names(prefix, len(metadata))
    if sorted(metadata) != names:
        raise AuthorityWireError("authority tensor payload contains non-canonical names")
    for name in names:
        entry = metadata[name]
        if not isinstance(entry, dict) or set(entry) != _MANIFEST_FIELDS:
            raise AuthorityWireError("authority tensor manifest has an invalid entry")
        shape = entry["shape"]
        stride = entry["stride"]
        if (
            not isinstance(entry["dtype"], str)
            or not isinstance(shape, list)
            or not isinstance(stride, list)
            or any(type(value) is not int for value in shape)
            or any(type(value) is not int for value in stride)
            or type(entry["storage_offset"]) is not int
            or type(entry["bytes"]) is not int
            or entry["bytes"] < 0
        ):
            raise AuthorityWireError("authority tensor manifest contains invalid ABI metadata")
    return names


def deserialize_tensor_list(payload: bytes, *, prefix: str) -> tuple[list[Any], str]:
    """Decode a bounded safetensors payload and reconstruct its exact ABI."""

    if not payload:
        return [], canonical_authority_digest({})
    import torch
    from safetensors.torch import load

    manifest, safetensors_payload = _decode_metadata(payload, _TENSOR_MAGIC)
    names = _validate_manifest(manifest, prefix)
    if not names:
        raise AuthorityWireError("empty authority tensor lists must use the canonical empty payload")
    try:
        storages = load(safetensors_payload)
    except Exception as exc:
        raise AuthorityWireError("authority tensor payload contained invalid safetensors storage") from exc
    if sorted(storages) != names:
        raise AuthorityWireError("authority tensor storage contains non-canonical names")

    tensors: dict[str, Any] = {}
    for name in names:
        entry = manifest[name]
        storage = storages[name]
        shape = tuple(entry["shape"])
        stride = tuple(entry["stride"])
        storage_offset = entry["storage_offset"]
        if storage.ndim != 1 or not storage.is_contiguous() or storage.storage_offset() != 0:
            raise AuthorityWireError("authority tensor backing storage is not canonical")
        if str(storage.dtype) != entry["dtype"]:
            raise AuthorityWireError("authority tensor dtype does not match its manifest")
        expected_elements = _checked_layout(shape, stride, storage_offset, int(storage.element_size()))
        logical_elements = 1
        for dimension in shape:
            logical_elements *= dimension
        if storage.numel() != expected_elements or entry["bytes"] != logical_elements * storage.element_size():
            raise AuthorityWireError("authority tensor storage size does not match its manifest")
        try:
            tensors[name] = torch.as_strided(storage, shape, stride, storage_offset)
        except RuntimeError as exc:
            raise AuthorityWireError("authority tensor manifest declared an invalid storage view") from exc

    decoded_manifest = _tensor_manifest(tensors)
    if decoded_manifest != manifest:
        raise AuthorityWireError("authority tensor ABI does not match its canonical manifest")
    return [tensors[name] for name in names], canonical_authority_digest(decoded_manifest)


def copy_tensor_to_device_preserving_abi(value: Any, *, device: str) -> Any:
    """Copy canonical tensor storage to a device without compacting its view."""

    import torch

    _validate_source_tensors([value])
    shape = tuple(int(dimension) for dimension in value.shape)
    stride = tuple(int(step) for step in value.stride())
    storage_offset = int(value.storage_offset())
    storage_elements = _checked_layout(shape, stride, storage_offset, int(value.element_size()))
    try:
        if value.numel() == 0:
            target_storage = torch.zeros(storage_elements, dtype=value.dtype, device=device)
        else:
            source_storage = torch.as_strided(value, (storage_elements,), (1,), 0)
            target_storage = source_storage.to(device=device, non_blocking=False, copy=True)
        copied = torch.as_strided(target_storage, shape, stride, storage_offset)
    except (RuntimeError, TypeError) as exc:
        raise AuthorityWireError("device transfer could not preserve the authority tensor ABI") from exc
    if (
        copied.dtype != value.dtype
        or copied.shape != value.shape
        or copied.stride() != value.stride()
        or copied.storage_offset() != value.storage_offset()
    ):
        raise AuthorityWireError("device transfer changed the authority tensor ABI")
    return copied


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate authority tensor metadata key")
        result[key] = value
    return result


__all__ = [
    "copy_tensor_to_device_preserving_abi",
    "deserialize_tensor_list",
    "serialize_tensor_list",
]
