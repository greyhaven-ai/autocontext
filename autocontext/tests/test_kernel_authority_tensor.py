from __future__ import annotations

import json
import struct

import pytest

from autocontext.kernel_evolution import AuthorityWireError, canonical_authority_digest
from autocontext.kernel_evolution.authority_tensor import (
    copy_tensor_to_device_preserving_abi,
    deserialize_tensor_list,
    serialize_tensor_list,
)

torch = pytest.importorskip("torch")
pytest.importorskip("safetensors")


def test_authority_tensor_round_trip_preserves_transposed_offset_abi() -> None:
    base = torch.arange(42, dtype=torch.float32).reshape(6, 7)
    value = base[1:6, 1:6].t()
    assert value.storage_offset() == 8
    assert not value.is_contiguous()

    payload, manifest_digest = serialize_tensor_list([value], prefix="input")
    decoded, decoded_manifest_digest = deserialize_tensor_list(payload, prefix="input")

    assert len(decoded) == 1
    restored = decoded[0]
    assert restored.dtype == value.dtype
    assert restored.shape == value.shape
    assert restored.stride() == value.stride()
    assert restored.storage_offset() == value.storage_offset()
    assert torch.equal(restored, value)
    assert decoded_manifest_digest == manifest_digest
    assert serialize_tensor_list(decoded, prefix="input") == (payload, manifest_digest)


def test_authority_tensor_round_trip_preserves_non_dense_stride() -> None:
    value = torch.arange(20, dtype=torch.int64)[2:18:3]

    payload, _manifest_digest = serialize_tensor_list([value], prefix="output")
    [restored], _decoded_manifest_digest = deserialize_tensor_list(payload, prefix="output")

    assert restored.shape == value.shape
    assert restored.stride() == value.stride()
    assert restored.storage_offset() == value.storage_offset()
    assert torch.equal(restored, value)


def test_authority_tensor_round_trip_accepts_irregular_non_overlapping_stride() -> None:
    value = torch.as_strided(torch.arange(18), (2, 2, 2), (4, 6, 7))
    assert len({i * 4 + j * 6 + k * 7 for i in range(2) for j in range(2) for k in range(2)}) == value.numel()

    payload, _manifest_digest = serialize_tensor_list([value], prefix="input")
    [restored], _decoded_manifest_digest = deserialize_tensor_list(payload, prefix="input")

    assert restored.shape == value.shape
    assert restored.stride() == value.stride()
    assert restored.storage_offset() == value.storage_offset()
    assert torch.equal(restored, value)


def test_authority_tensor_device_copy_preserves_transposed_offset_abi_on_cpu() -> None:
    value = torch.arange(30, dtype=torch.float32).reshape(5, 6)[1:, 1:5].t()

    copied = copy_tensor_to_device_preserving_abi(value, device="cpu")

    assert copied.data_ptr() != value.data_ptr()
    assert copied.shape == value.shape
    assert copied.stride() == value.stride()
    assert copied.storage_offset() == value.storage_offset()
    assert torch.equal(copied, value)


def test_authority_tensor_empty_offset_round_trip_and_device_copy_are_consistent() -> None:
    value = torch.as_strided(torch.empty(0), (0,), (1,), 100)

    payload, _manifest_digest = serialize_tensor_list([value], prefix="input")
    [restored], _decoded_manifest_digest = deserialize_tensor_list(payload, prefix="input")
    copied = copy_tensor_to_device_preserving_abi(value, device="cpu")

    for result in (restored, copied):
        assert result.shape == value.shape
        assert result.stride() == value.stride()
        assert result.storage_offset() == value.storage_offset() == 100
        assert torch.equal(result, value)


def test_authority_tensor_empty_list_keeps_canonical_empty_commitment() -> None:
    payload, manifest_digest = serialize_tensor_list([], prefix="input")

    assert payload == b""
    assert manifest_digest == canonical_authority_digest({})
    assert deserialize_tensor_list(payload, prefix="input") == ([], manifest_digest)


def test_authority_tensor_rejects_noncanonical_framed_empty_list() -> None:
    payload = struct.pack("!8sI", b"ACTENS2\0", 2) + b"{}"

    with pytest.raises(AuthorityWireError, match="canonical empty payload"):
        deserialize_tensor_list(payload, prefix="input")


@pytest.mark.parametrize(
    "values",
    [
        lambda: [torch.arange(8), torch.arange(8)],
        lambda: [torch.arange(8).as_strided((2, 2), (1, 1))],
        lambda: [torch.ones(1, 3).expand(4, 3)],
    ],
    ids=("independent", "internal-overlap", "expanded-overlap"),
)
def test_authority_tensor_overlap_validation_accepts_only_independent_storage(values) -> None:
    tensors = values()
    if len(tensors) == 2:
        serialize_tensor_list(tensors, prefix="input")
        return

    with pytest.raises(AuthorityWireError, match="overlapping storage"):
        serialize_tensor_list(tensors, prefix="input")


def test_authority_tensor_rejects_cross_value_storage_aliases() -> None:
    storage = torch.arange(12)

    with pytest.raises(AuthorityWireError, match="storage aliases"):
        serialize_tensor_list([storage[:6], storage[6:]], prefix="input")


def test_authority_tensor_decoder_rejects_forged_overlapping_manifest() -> None:
    payload, _manifest_digest = serialize_tensor_list([torch.arange(4).reshape(2, 2)], prefix="input")
    magic, metadata_size = struct.unpack("!8sI", payload[:12])
    metadata = json.loads(payload[12 : 12 + metadata_size])
    metadata["input_0000"]["stride"] = [1, 1]
    forged_metadata = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    forged = struct.pack("!8sI", magic, len(forged_metadata)) + forged_metadata + payload[12 + metadata_size :]

    with pytest.raises(AuthorityWireError, match="overlapping storage"):
        deserialize_tensor_list(forged, prefix="input")


def test_authority_tensor_exact_layout_check_rejects_irregular_overlap() -> None:
    value = torch.as_strided(torch.arange(21), (2, 2, 2), (4, 6, 10))

    with pytest.raises(AuthorityWireError, match="overlapping storage"):
        serialize_tensor_list([value], prefix="input")


def test_authority_tensor_count_limit_precedes_name_allocation() -> None:
    with pytest.raises(AuthorityWireError, match="too many tensors"):
        serialize_tensor_list([object()] * 4097, prefix="input")


def test_authority_tensor_rejects_noncanonical_prefix() -> None:
    with pytest.raises(ValueError, match="safe canonical name"):
        serialize_tensor_list([torch.ones(1)], prefix="../input")
