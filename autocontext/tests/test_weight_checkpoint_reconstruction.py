"""Small authored tensors exercise the real reconstruction/verification pipeline.

The synthetic lock is deliberately constructed below the fixed public Qwen
schema; no production command can load this reduced-size profile from JSON.
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest

from autocontext.weight_evolution.artifacts import (
    CheckpointError,
    TensorFile,
    TensorInfo,
    ensure_finite,
    file_identity,
    write_tensor_file,
)
from autocontext.weight_evolution.models import ReproductionLock, load_reproduction_lock
from autocontext.weight_evolution.patch_worker import (
    _load_delta,
    _patched_tensor,
    reconstruct_checkpoint,
    verify_checkpoint,
)

LOCK = Path(__file__).resolve().parents[2] / "examples/weight_evolution/qwen_reproduction/reproduction.lock.json"


def _bits(*values: int) -> bytes:
    return np.array(values, dtype="<u2").tobytes()


def _write_tensors(path: Path, entries: dict[str, tuple[str, tuple[int, ...], bytes]]) -> None:
    tensors = tuple(TensorInfo(name, dt, shape, 0, len(data)) for name, (dt, shape, data) in sorted(entries.items()))
    write_tensor_file(path, tensors, lambda name: entries[name][2])


@pytest.fixture
def fixture_checkpoint(tmp_path: Path) -> tuple[ReproductionLock, Path]:
    public = load_reproduction_lock(LOCK)
    root = tmp_path / "assets"
    root.mkdir()
    original = _bits(0x3F80, 0x4000, 0x4040, 0x4080, 0x40A0, 0x40C0)
    edited = _bits(0x3F00, 0x4000, 0x4040, 0x4080, 0x40B0, 0x40C0)
    pins = tuple(pin.model_copy(update={
        "shape": (2, 3), "changed_entries": 2, "changed_rows": 2, "changed_columns": 2,
        "base_sha256": hashlib.sha256(original).hexdigest(), "edited_sha256": hashlib.sha256(edited).hexdigest(),
    }) for pin in public.patch.tensors)
    patch = public.patch.model_copy(update={"tensors": pins, "changed_entries": 4, "touched_entries": 8})
    assets = []
    for asset in public.assets:
        path = root / asset.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if asset.asset_id == "base_weights":
            entries = {pin.name: ("BF16", (2, 3), original) for pin in pins}
            entries["model.language_model.embed_tokens.weight"] = ("BF16", (2, 3), original)
            entries["model.language_model.norm.weight"] = ("F32", (2,), np.array([1, 2], dtype="<f4").tobytes())
            entries["model.visual.patch_embed.proj.weight"] = ("BF16", (1,), _bits(0x3F80))
            entries["mtp.fc.weight"] = ("BF16", (1,), _bits(0x3F80))
            _write_tensors(path, entries)
        elif asset.asset_id == "patch_delta":
            np.savez(path, t0_idx=np.array([0, 4], dtype="<i4"), t0_val=np.array([0x3F00, 0x40B0], dtype="<u2"),
                     t1_idx=np.array([0, 4], dtype="<i4"), t1_val=np.array([0x3F00, 0x40B0], dtype="<u2"))
        elif asset.asset_id == "patch_manifest":
            path.write_text(json.dumps({"tensors": [pin.to_dict() for pin in pins], "changed_tensors": 2,
                                        "changed_entries_total": 4}))
        else:
            path.write_text("{}" if path.suffix == ".json" else "synthetic fixture\n")
        identity = file_identity(path)
        assets.append(asset.model_copy(update={"sha256": identity["sha256"], "size_bytes": identity["size_bytes"]}))
    return public.model_copy(update={"assets": tuple(assets), "patch": patch}), root


@pytest.mark.parametrize("variant", ["base", "published_patch"])
def test_round_trip_authenticates_all_text_tensors_and_config(fixture_checkpoint, tmp_path, variant):
    lock, assets = fixture_checkpoint
    output = tmp_path / variant
    receipt = reconstruct_checkpoint(lock, assets, output, variant=variant)
    checked = verify_checkpoint(lock, assets, output, expected_variant=variant)
    assert checked == receipt
    assert receipt.variant == variant
    assert receipt.changed_entries == (4 if variant == "published_patch" else 0)
    assert receipt.selected_entries == (8 if variant == "published_patch" else 0)
    assert receipt.excluded_tensors == ("model.visual.patch_embed.proj.weight", "mtp.fc.weight")
    assert tuple(record.name for record in receipt.excluded_tensor_records) == receipt.excluded_tensors
    assert len(receipt.tensors) == 4
    assert {t.name for t in receipt.tensors} == set(TensorFile(output / "model.safetensors").tensors)
    assert len(receipt.files) == 6
    assert len(receipt.tensor_content_digest) == 64
    assert all(t.base_sha256 == t.sha256 for t in receipt.tensors if t.changed_entries == 0)
    with pytest.raises(CheckpointError, match="already exist"):
        reconstruct_checkpoint(lock, assets, output, variant=variant)


def test_variant_changes_content_identity(fixture_checkpoint, tmp_path):
    lock, assets = fixture_checkpoint
    base = reconstruct_checkpoint(lock, assets, tmp_path / "base", variant="base")
    patched = reconstruct_checkpoint(lock, assets, tmp_path / "edited")
    assert base.digest != patched.digest
    assert base.tensor_content_digest != patched.tensor_content_digest
    with pytest.raises(CheckpointError, match="variant"):
        verify_checkpoint(lock, assets, tmp_path / "edited", expected_variant="base")


@pytest.mark.parametrize("filename", ["config.json", "tokenizer.json", "chat_template.jinja", "generation_config.json"])
def test_changed_loader_assets_are_rejected(fixture_checkpoint, tmp_path, filename):
    lock, assets = fixture_checkpoint
    output = tmp_path / "output"
    reconstruct_checkpoint(lock, assets, output)
    (output / filename).write_text("corrupted")
    with pytest.raises(CheckpointError, match="configuration/tokenizer"):
        verify_checkpoint(lock, assets, output)


@pytest.mark.parametrize("name", ["model.language_model.embed_tokens.weight",
                                 "model.language_model.layers.15.mlp.down_proj.weight"])
def test_receipt_cannot_authorize_altered_protected_or_edited_tensor(fixture_checkpoint, tmp_path, name):
    lock, assets = fixture_checkpoint
    output = tmp_path / "output"
    reconstruct_checkpoint(lock, assets, output)
    path = output / "model.safetensors"
    container = TensorFile(path)
    with path.open("r+b") as stream:
        stream.seek(container.data_start + container.tensors[name].start)
        stream.write(_bits(0x4000))
    payload = json.loads((output / "checkpoint.receipt.json").read_text())
    payload["checkpoint_sha256"] = file_identity(path)["sha256"]
    (output / "checkpoint.receipt.json").write_text(json.dumps(payload))
    with pytest.raises(CheckpointError, match="independently reconstructed"):
        verify_checkpoint(lock, assets, output)


def test_receipt_tampering_is_rejected(fixture_checkpoint, tmp_path):
    lock, assets = fixture_checkpoint
    output = tmp_path / "output"
    reconstruct_checkpoint(lock, assets, output)
    path = output / "checkpoint.receipt.json"
    payload = json.loads(path.read_text())
    payload["changed_entries"] = 1
    path.write_text(json.dumps(payload))
    with pytest.raises(CheckpointError, match="receipt disagrees"):
        verify_checkpoint(lock, assets, output)


@pytest.mark.parametrize("extra", ["modeling_qwen.py", "pytorch_model.bin", "extra.json"])
def test_unexpected_files_are_rejected_before_loading(fixture_checkpoint, tmp_path, extra):
    lock, assets = fixture_checkpoint
    output = tmp_path / "output"
    reconstruct_checkpoint(lock, assets, output)
    (output / extra).write_text("never execute")
    with pytest.raises(CheckpointError, match="unexpected files"):
        verify_checkpoint(lock, assets, output)


def test_corrupted_source_fails_without_partial_publication(fixture_checkpoint, tmp_path):
    lock, assets = fixture_checkpoint
    pin = next(a for a in lock.assets if a.asset_id == "base_weights")
    (assets / pin.relative_path).write_bytes(b"bad input")
    with pytest.raises(CheckpointError, match="authentication"):
        reconstruct_checkpoint(lock, assets, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".output-*"))


def test_failed_reconstruction_cleans_staging_directory(fixture_checkpoint, tmp_path, monkeypatch):
    from autocontext.weight_evolution import patch_worker

    lock, assets = fixture_checkpoint

    def fail(*args):
        raise CheckpointError("simulated verification failure")

    monkeypatch.setattr(patch_worker, "_inspect_checkpoint", fail)
    with pytest.raises(CheckpointError, match="simulated"):
        reconstruct_checkpoint(lock, assets, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".output-*"))


def test_symlink_configuration_is_rejected(fixture_checkpoint, tmp_path):
    lock, assets = fixture_checkpoint
    output = tmp_path / "output"
    reconstruct_checkpoint(lock, assets, output)
    path = output / "config.json"
    copy = tmp_path / "config.json"
    path.rename(copy)
    path.symlink_to(copy)
    with pytest.raises(CheckpointError, match="symlink"):
        verify_checkpoint(lock, assets, output)


def test_directory_in_place_of_regular_file_is_rejected(tmp_path):
    with pytest.raises(CheckpointError, match="regular file"):
        file_identity(tmp_path)


def test_existing_empty_output_is_preserved(fixture_checkpoint, tmp_path):
    lock, assets = fixture_checkpoint
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(CheckpointError, match="already exist"):
        reconstruct_checkpoint(lock, assets, output)
    assert output.is_dir() and not list(output.iterdir())


@pytest.mark.parametrize("change", ["duplicate", "negative", "out_of_bounds", "object", "wrong_dtype", "nonfinite", "unchanged"])
def test_bad_sparse_delta_is_rejected(fixture_checkpoint, tmp_path, change):
    lock, _ = fixture_checkpoint
    pin = lock.patch.tensors[0]
    indices = np.array([0, 4], dtype="<i4")
    values = np.array([0x3F00, 0x40B0], dtype="<u2")
    if change == "duplicate":
        indices[1] = 0
    elif change == "negative":
        indices[0] = -1
    elif change == "out_of_bounds":
        indices[1] = 6
    elif change == "object":
        values = np.array([{}, {}], dtype=object)
    elif change == "wrong_dtype":
        indices = indices.astype("<i8")
    elif change == "nonfinite":
        values[0] = 0x7F80
    elif change == "unchanged":
        values[0] = 0x3F80
    path = tmp_path / "delta.npz"
    np.savez(path, t0_idx=indices, t0_val=values)
    with pytest.raises((CheckpointError, ValueError)):
        delta = _load_delta(path, (pin,))
        _patched_tensor(_bits(0x3F80, 0x4000, 0x4040, 0x4080, 0x40A0, 0x40C0), pin, *delta[pin.name])


def test_duplicate_archive_members_rejected(fixture_checkpoint, tmp_path):
    lock, _ = fixture_checkpoint
    path = tmp_path / "delta.npz"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("t0_idx.npy", b"x")
        archive.writestr("t0_val.npy", b"x")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("t0_idx.npy", b"x")
    with pytest.raises(CheckpointError, match="duplicate"):
        _load_delta(path, (lock.patch.tensors[0],))


def test_huge_declared_array_rejected_before_numpy_allocation(fixture_checkpoint, tmp_path, monkeypatch):
    lock, _ = fixture_checkpoint
    path = tmp_path / "delta.npz"
    with zipfile.ZipFile(path, "w") as archive:
        for suffix, dtype in (("idx", "<i4"), ("val", "<u2")):
            header = io.BytesIO()
            np.lib.format.write_array_header_1_0(header, {"descr": dtype, "fortran_order": False, "shape": (2**40,)})
            archive.writestr(f"t0_{suffix}.npy", header.getvalue())

    def forbidden_load(*args, **kwargs):
        pytest.fail("np.load must not see an unbounded NPY shape")

    monkeypatch.setattr(np, "load", forbidden_load)
    with pytest.raises(CheckpointError, match="header differs"):
        _load_delta(path, (lock.patch.tensors[0],))


@pytest.mark.parametrize("dtype,data", [("BF16", _bits(0x7F80)), ("BF16", _bits(0x7FC0)),
                                       ("F32", struct.pack("<f", float("inf"))), ("F16", struct.pack("<e", float("nan")))])
def test_nonfinite_tensors_rejected(dtype, data):
    with pytest.raises(CheckpointError, match="nonfinite"):
        ensure_finite(data, dtype)


@pytest.mark.parametrize("header,data", [
    (b'{"a":{"dtype":"BF16","shape":[1],"data_offsets":[0,2]},"a":{}}', b"00"),
    (b'{"a":{"dtype":"BF16","shape":[1],"data_offsets":[1,3]}}', b"000"),
    (b'{"a":{"dtype":"BF16","shape":[1],"data_offsets":[0,2]}}', b"000"),
    (b'{"a":{"dtype":"U16","shape":[1],"data_offsets":[0,2]}}', b"00"),
    (b'{"a":{"dtype":"BF16","shape":[true],"data_offsets":[0,2]}}', b"00"),
    (b'{"a":{"dtype":"BF16","shape":[2],"data_offsets":[0,2]}}', b"00"),
    (b'{"a":{"dtype":"BF16","shape":[1],"data_offsets":[0,4]}}', b"00"),
])
def test_malformed_safetensors_rejected(tmp_path, header, data):
    path = tmp_path / "bad.safetensors"
    path.write_bytes(struct.pack("<Q", len(header)) + header + data)
    with pytest.raises(CheckpointError):
        TensorFile(path)


def test_oversized_header_rejected_before_allocation(tmp_path):
    path = tmp_path / "bad.safetensors"
    path.write_bytes(struct.pack("<Q", 2**63) + b"{}")
    with pytest.raises(CheckpointError, match="header length"):
        TensorFile(path)
