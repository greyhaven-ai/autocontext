"""Reconstruct the published sparse BF16 patch and independently verify exports.

Call these data-only functions inside the isolated reconstruction/inference
worker. They do not execute downloaded code, contact the network, or import a
model runtime. Verification derives every tensor from authenticated base bytes
and does not treat a worker's receipt as a trust anchor.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Literal

from autocontext.util.file_lock import advisory_path_lock
from autocontext.util.json_io import write_json
from autocontext.weight_evolution.artifacts import (
    CheckpointError,
    CheckpointReceipt,
    TensorFile,
    TensorVerification,
    canonical_digest,
    ensure_finite,
    file_identity,
    load_small_json,
    regular_file,
    write_tensor_file,
)
from autocontext.weight_evolution.assets import verify_assets
from autocontext.weight_evolution.models import ReproductionLock, TensorPin

Variant = Literal["base", "published_patch"]
_CONFIG_FILES = {
    "text_config": "config.json",
    "generation_config": "generation_config.json",
    "tokenizer_config": "tokenizer_config.json",
    "chat_template": "chat_template.jinja",
    "tokenizer_vocabulary": "tokenizer.json",
}
_RECEIPT = "checkpoint.receipt.json"


def _verified_inputs(lock: ReproductionLock, root: Path) -> dict[str, Path]:
    selected = tuple(asset.asset_id for asset in lock.assets if asset.required) + ("base_weights", "tokenizer_vocabulary")
    report = verify_assets(lock, root, asset_ids=selected)
    if not report.ok:
        failures = "; ".join(f"{item.asset_id}: {item.detail}" for item in report.assets if item.status != "verified")
        raise CheckpointError(f"checkpoint inputs failed authentication: {failures}")
    return {asset.asset_id: root / asset.relative_path for asset in lock.assets}


def _load_delta(path: Path, pins: tuple[TensorPin, ...]) -> dict[str, tuple[Any, Any]]:
    import numpy as np

    expected = {f"t{i}_{suffix}.npy" for i in range(len(pins)) for suffix in ("idx", "val")}
    # Inspect ZIP size declarations before NumPy allocates any arrays. No pickle,
    # object arrays, duplicate members, auxiliary members, or oversized headers.
    with regular_file(path) as stream, zipfile.ZipFile(stream) as archive:
        members = archive.infolist()
        if len(members) != len(expected) or {item.filename for item in members} != expected:
            raise CheckpointError("patch archive has missing, extra, or duplicate arrays")
        for i, pin in enumerate(pins):
            for suffix, size in (("idx", 4), ("val", 2)):
                info = archive.getinfo(f"t{i}_{suffix}.npy")
                if info.file_size > pin.changed_entries * size + 1024:
                    raise CheckpointError("patch array exceeds its bounded size")
                if info.flag_bits & 1:
                    raise CheckpointError("encrypted patch arrays are not supported")
                with archive.open(info) as member:
                    version = np.lib.format.read_magic(member)
                    if version == (1, 0):
                        shape, fortran_order, dtype = np.lib.format.read_array_header_1_0(member, max_header_size=1024)
                    elif version == (2, 0):
                        shape, fortran_order, dtype = np.lib.format.read_array_header_2_0(member, max_header_size=1024)
                    else:
                        raise CheckpointError("unsupported patch array format version")
                    expected_dtype = np.dtype("<i4" if suffix == "idx" else "<u2")
                    if shape != (pin.changed_entries,) or fortran_order or dtype != expected_dtype:
                        raise CheckpointError("patch array header differs from the bounded shape/dtype")
                    if info.file_size != member.tell() + pin.changed_entries * size:
                        raise CheckpointError("patch array payload size differs from its header")
    result = {}
    with regular_file(path) as stream, np.load(stream, allow_pickle=False, max_header_size=1024) as arrays:
        for i, pin in enumerate(pins):
            indices, values = arrays[f"t{i}_idx"], arrays[f"t{i}_val"]
            if indices.dtype != np.dtype("<i4") or values.dtype != np.dtype("<u2"):
                raise CheckpointError("patch array dtypes must be int32 indices and uint16 BF16 bits")
            if indices.shape != (pin.changed_entries,) or values.shape != indices.shape:
                raise CheckpointError("patch array shape/count differs from the manifest")
            if np.any(indices < 0) or np.any(indices >= pin.shape[0] * pin.shape[1]):
                raise CheckpointError("patch index is outside the tensor")
            if np.unique(indices).size != indices.size:
                raise CheckpointError("patch indices are not unique")
            if np.unique(indices % pin.shape[1]).size != pin.changed_columns:
                raise CheckpointError("changed-column count differs from the manifest")
            if np.unique(indices // pin.shape[1]).size != pin.changed_rows:
                raise CheckpointError("changed-row count differs from the manifest")
            ensure_finite(values.tobytes(), "BF16")
            result[pin.name] = (indices, values)
    return result


def _patched_tensor(base: bytes, pin: TensorPin, indices: Any, values: Any) -> bytes:
    import numpy as np

    if hashlib.sha256(base).hexdigest() != pin.base_sha256:
        raise CheckpointError(f"base tensor hash differs from the published patch: {pin.name}")
    bits = np.frombuffer(base, dtype="<u2").copy()
    if len(base) != pin.shape[0] * pin.shape[1] * 2:
        raise CheckpointError("base tensor size differs from the patch")
    if np.count_nonzero(bits[indices] != values) != pin.changed_entries:
        raise CheckpointError("delta contains unchanged entries or a wrong changed-entry count")
    bits[indices] = values
    edited = bits.tobytes()
    ensure_finite(edited, "BF16")
    if hashlib.sha256(edited).hexdigest() != pin.edited_sha256:
        raise CheckpointError(f"reconstructed tensor hash differs from the published patch: {pin.name}")
    return edited


def _text_tensors(base: TensorFile, lock: ReproductionLock) -> tuple[tuple[str, ...], tuple[str, ...]]:
    retained, excluded = [], []
    for name in sorted(base.tensors):
        if name.startswith(lock.model.tensor_namespace) or name == "lm_head.weight":
            retained.append(name)
        elif name.startswith(("model.visual.", "mtp.")):
            excluded.append(name)
        else:
            raise CheckpointError(f"unrecognized checkpoint tensor namespace: {name}")
    if not retained or not {pin.name for pin in lock.patch.tensors}.issubset(retained):
        raise CheckpointError("base checkpoint lacks the required text tensors")
    for pin in lock.patch.tensors:
        info = base.tensors[pin.name]
        if info.dtype != "BF16" or info.shape != pin.shape:
            raise CheckpointError(f"patch tensor shape/dtype differs: {pin.name}")
    return tuple(retained), tuple(excluded)


def _inspect_checkpoint(
    lock: ReproductionLock, inputs: dict[str, Path], directory: Path, variant: Variant,
) -> CheckpointReceipt:
    base, candidate = TensorFile(inputs["base_weights"]), TensorFile(directory / "model.safetensors")
    retained, excluded = _text_tensors(base, lock)
    if set(candidate.tensors) != set(retained):
        raise CheckpointError("export has missing, extra, or protected tensor names")
    deltas = _load_delta(inputs[lock.patch.delta_asset_id], lock.patch.tensors)
    pins = {pin.name: pin for pin in lock.patch.tensors}
    records = []
    for name in retained:
        source_info, output_info = base.tensors[name], candidate.tensors[name]
        if (source_info.shape, source_info.dtype) != (output_info.shape, output_info.dtype):
            raise CheckpointError(f"export changed tensor shape/dtype: {name}")
        original = base.read(name)
        expected = original
        if name in pins:
            # Even base export validates that these are the published base
            # tensors and that its paired patch remains reconstructible.
            rebuilt = _patched_tensor(original, pins[name], *deltas[name])
            if variant == "published_patch":
                expected = rebuilt
        actual = candidate.read(name)
        if actual != expected:
            raise CheckpointError(f"export tensor differs from independently reconstructed bytes: {name}")
        records.append(TensorVerification(
            name, output_info.dtype, output_info.shape, hashlib.sha256(original).hexdigest(),
            hashlib.sha256(actual).hexdigest(),
            pins[name].changed_entries if name in pins and variant == "published_patch" else 0,
        ))
    files = [file_identity(directory / "model.safetensors")]
    asset_pins = {asset.asset_id: asset for asset in lock.assets}
    for asset_id, filename in _CONFIG_FILES.items():
        identity = file_identity(directory / filename)
        pin = asset_pins[asset_id]
        if identity["sha256"] != pin.sha256 or identity["size_bytes"] != pin.size_bytes:
            raise CheckpointError(f"export configuration/tokenizer differs from its pin: {filename}")
        files.append(identity)
    excluded_records = []
    for name in excluded:
        info = base.tensors[name]
        digest = hashlib.sha256(base.read(name)).hexdigest()
        excluded_records.append(TensorVerification(name, info.dtype, info.shape, digest, digest, 0))
    inventory = [{"name": r.name, "dtype": r.dtype, "shape": r.shape, "sha256": r.sha256} for r in records]
    return CheckpointReceipt(
        variant, lock.digest, str(files[0]["sha256"]), canonical_digest(inventory),
        tuple(files), tuple(records), excluded, tuple(excluded_records), sum(r.changed_entries for r in records),
        lock.patch.touched_entries if variant == "published_patch" else 0,
    )


def reconstruct_checkpoint(
    lock: ReproductionLock, asset_root: Path, output_dir: Path, *, variant: Variant = "published_patch",
) -> CheckpointReceipt:
    """Publish a new complete text checkpoint directory after independent checks.

    Inputs stay immutable. An existing output, including an empty directory, is
    refused. Interrupted work is cleaned up; concurrent cooperating publishers
    share the repository's advisory-file-lock primitive.
    """
    if variant not in ("base", "published_patch"):
        raise CheckpointError("unknown checkpoint variant")
    root = asset_root.resolve(strict=True)
    output_dir = output_dir.absolute()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink() or output_dir.exists():
        raise CheckpointError("checkpoint output must not already exist")
    output_dir = output_dir.parent.resolve(strict=True) / output_dir.name
    inputs = _verified_inputs(lock, root)
    base = TensorFile(inputs["base_weights"])
    names, _ = _text_tensors(base, lock)
    deltas = _load_delta(inputs[lock.patch.delta_asset_id], lock.patch.tensors)
    pins = {pin.name: pin for pin in lock.patch.tensors}

    def tensor_bytes(name: str) -> bytes:
        data = base.read(name)
        if name in pins:
            rebuilt = _patched_tensor(data, pins[name], *deltas[name])
            if variant == "published_patch":
                return rebuilt
        return data

    with advisory_path_lock(output_dir.parent / f".{output_dir.name}.lock"):
        if output_dir.exists() or output_dir.is_symlink():
            raise CheckpointError("checkpoint output must not already exist")
        temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
        try:
            write_tensor_file(temporary / "model.safetensors", tuple(base.tensors[name] for name in names), tensor_bytes)
            for asset_id, filename in _CONFIG_FILES.items():
                shutil.copyfile(inputs[asset_id], temporary / filename)
            receipt = _inspect_checkpoint(lock, inputs, temporary, variant)
            # Detect mutation of a source asset during reconstruction before
            # publishing evidence; the container also mounts these read-only.
            _verified_inputs(lock, root)
            write_json(temporary / _RECEIPT, receipt.to_dict())
            os.rename(temporary, output_dir)
            return receipt
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)


def verify_checkpoint(
    lock: ReproductionLock, asset_root: Path, checkpoint_dir: Path, *, expected_variant: Variant | None = None,
) -> CheckpointReceipt:
    """Authenticate every output tensor/config against source assets, not claims.

    The receipt's variant chooses one of two fixed transformations; callers can
    additionally require a variant. Unexpected files (including Python modules
    or alternative weight formats) are refused before any model loader runs.
    """
    if checkpoint_dir.is_symlink():
        raise CheckpointError("symlink checkpoint directory is not allowed")
    directory = checkpoint_dir.resolve(strict=True)
    expected_files = {"model.safetensors", _RECEIPT, *_CONFIG_FILES.values()}
    if {path.name for path in directory.iterdir()} != expected_files:
        raise CheckpointError("checkpoint directory has missing or unexpected files")
    payload = load_small_json(directory / _RECEIPT)
    if not isinstance(payload, dict) or payload.get("variant") not in ("base", "published_patch"):
        raise CheckpointError("invalid checkpoint receipt variant")
    variant = payload["variant"]
    if expected_variant is not None and expected_variant != variant:
        raise CheckpointError("checkpoint variant does not match the requested variant")
    root = asset_root.resolve(strict=True)
    inputs = _verified_inputs(lock, root)
    receipt = _inspect_checkpoint(lock, inputs, directory, variant)
    # JSON normalizes tuples to lists; compare canonical serialized content.
    if canonical_digest(payload) != receipt.digest:
        raise CheckpointError("checkpoint receipt disagrees with independently verified content")
    _verified_inputs(lock, root)
    return receipt
