"""Data-only, bounded safetensors I/O and checkpoint content identities.

This deliberately supports only the byte-aligned floating dtypes in the Qwen
export. BF16 is handled as little-endian uint16 bits, never rounded through a
different floating dtype. Format: https://github.com/huggingface/safetensors#format.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import struct
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO

MAX_HEADER_BYTES = 4 * 1024 * 1024
MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024 * 1024
_DTYPES = {"BF16": 2, "F16": 2, "F32": 4, "F64": 8}


class CheckpointError(ValueError):
    """The checkpoint is malformed or differs from its pinned reconstruction."""


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise CheckpointError(f"duplicate JSON key: {name}")
        result[name] = value
    return result


@contextmanager
def regular_file(path: Path) -> Iterator[BinaryIO]:
    """Refuse symlink leaves and special files before reading any bytes."""
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise CheckpointError(f"symlink checkpoint path is not allowed: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise CheckpointError(f"not a regular file: {path}")
    with os.fdopen(descriptor, "rb") as stream:
        yield stream


def file_identity(path: Path, *, max_bytes: int = MAX_CHECKPOINT_BYTES) -> dict[str, Any]:
    digest = hashlib.sha256()
    total = 0
    with regular_file(path) as stream:
        if os.fstat(stream.fileno()).st_size > max_bytes:
            raise CheckpointError(f"file exceeds byte budget: {path.name}")
        while data := stream.read(min(1024 * 1024, max_bytes - total + 1)):
            total += len(data)
            if total > max_bytes:
                raise CheckpointError(f"file exceeds byte budget: {path.name}")
            digest.update(data)
    return {"name": path.name, "size_bytes": total, "sha256": digest.hexdigest()}


def load_small_json(path: Path, *, max_bytes: int = MAX_HEADER_BYTES) -> Any:
    with regular_file(path) as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise CheckpointError(f"JSON exceeds byte budget: {path.name}")
    return json.loads(data, object_pairs_hook=_unique_object)


@dataclass(frozen=True)
class TensorInfo:
    name: str
    dtype: str
    shape: tuple[int, ...]
    start: int
    end: int


class TensorFile:
    """Validated index with bounded per-tensor reads; no executable deserializer."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with regular_file(path) as stream:
            size = os.fstat(stream.fileno()).st_size
            if not 10 <= size <= MAX_CHECKPOINT_BYTES:
                raise CheckpointError("checkpoint size is outside the supported range")
            raw_length = stream.read(8)
            length = struct.unpack("<Q", raw_length)[0]
            if not 2 <= length <= min(MAX_HEADER_BYTES, size - 8):
                raise CheckpointError("invalid safetensors header length")
            header_bytes = stream.read(length)
        if not header_bytes.startswith(b"{"):
            raise CheckpointError("safetensors header must begin with an object")
        header = json.loads(header_bytes, object_pairs_hook=_unique_object)
        if not isinstance(header, dict) or not header:
            raise CheckpointError("safetensors header must be a nonempty object")
        self.data_start = 8 + length
        tensors: dict[str, TensorInfo] = {}
        for name, value in header.items():
            if name == "__metadata__":
                if not isinstance(value, dict) or any(not isinstance(v, str) for v in value.values()):
                    raise CheckpointError("invalid safetensors metadata")
                continue
            if not name or not isinstance(value, dict) or set(value) != {"dtype", "shape", "data_offsets"}:
                raise CheckpointError("invalid tensor header")
            dtype, shape, offsets = value["dtype"], value["shape"], value["data_offsets"]
            if not isinstance(dtype, str) or dtype not in _DTYPES:
                raise CheckpointError(f"unsupported tensor dtype: {dtype}")
            if not isinstance(shape, list) or len(shape) > 8 or any(type(n) is not int or n <= 0 for n in shape):
                raise CheckpointError("invalid or empty tensor shape")
            if not isinstance(offsets, list) or len(offsets) != 2 or any(type(n) is not int or n < 0 for n in offsets):
                raise CheckpointError("invalid tensor offsets")
            start, end = offsets
            if end - start != math.prod(shape) * _DTYPES[dtype] or end > size - self.data_start:
                raise CheckpointError("tensor size does not match its shape/dtype")
            tensors[name] = TensorInfo(name, dtype, tuple(shape), start, end)
        expected_offset = 0
        for tensor in sorted(tensors.values(), key=lambda item: item.start):
            if tensor.start != expected_offset:
                raise CheckpointError("tensor offsets overlap or leave unindexed bytes")
            expected_offset = tensor.end
        if not tensors or expected_offset != size - self.data_start:
            raise CheckpointError("checkpoint contains missing or trailing tensor bytes")
        self.tensors = tensors

    def read(self, name: str) -> bytes:
        tensor = self.tensors[name]
        with regular_file(self.path) as stream:
            stream.seek(self.data_start + tensor.start)
            data = stream.read(tensor.end - tensor.start)
        if len(data) != tensor.end - tensor.start:
            raise CheckpointError(f"truncated tensor: {name}")
        ensure_finite(data, tensor.dtype)
        return data


def ensure_finite(data: bytes, dtype: str) -> None:
    import numpy as np

    if dtype == "BF16":
        finite = not np.any((np.frombuffer(data, dtype="<u2") & 0x7F80) == 0x7F80)
    else:
        finite = bool(np.isfinite(np.frombuffer(data, dtype={"F16": "<f2", "F32": "<f4", "F64": "<f8"}[dtype])).all())
    if not finite:
        raise CheckpointError("nonfinite tensor value")


def write_tensor_file(path: Path, tensors: tuple[TensorInfo, ...], read: Callable[[str], bytes]) -> None:
    """Write a deterministic safetensors container from a validated byte supplier."""
    header: dict[str, Any] = {"__metadata__": {"format": "pt"}}
    offset = 0
    for tensor in tensors:
        size = math.prod(tensor.shape) * _DTYPES[tensor.dtype]
        header[tensor.name] = {"dtype": tensor.dtype, "shape": list(tensor.shape), "data_offsets": [offset, offset + size]}
        offset += size
    encoded = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    encoded += b" " * (-len(encoded) % 8)
    if len(encoded) > MAX_HEADER_BYTES or offset + len(encoded) + 8 > MAX_CHECKPOINT_BYTES:
        raise CheckpointError("serialized checkpoint exceeds byte budget")
    with path.open("xb") as stream:
        stream.write(struct.pack("<Q", len(encoded)))
        stream.write(encoded)
        for tensor in tensors:
            data = read(tensor.name)
            if len(data) != math.prod(tensor.shape) * _DTYPES[tensor.dtype]:
                raise CheckpointError("tensor supplier returned the wrong byte count")
            ensure_finite(data, tensor.dtype)
            stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class TensorVerification:
    name: str
    dtype: str
    shape: tuple[int, ...]
    base_sha256: str
    sha256: str
    changed_entries: int


@dataclass(frozen=True)
class CheckpointReceipt:
    variant: str
    lock_digest: str
    checkpoint_sha256: str
    tensor_content_digest: str
    files: tuple[dict[str, object], ...]
    tensors: tuple[TensorVerification, ...]
    excluded_tensors: tuple[str, ...]
    excluded_tensor_records: tuple[TensorVerification, ...]
    changed_entries: int
    selected_entries: int

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "stage": "checkpoint_verification", **asdict(self),
                "selected_entries_evidence": "manifest-derived; the released delta contains changed positions only"}

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())
