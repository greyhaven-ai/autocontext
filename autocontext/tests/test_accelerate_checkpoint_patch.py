"""Exercise the maintained build through real CPU checkpoint loading."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
accelerate = pytest.importorskip("accelerate")


def _weights():
    model = torch.nn.Linear(2, 1)
    with torch.no_grad():
        model.weight.fill_(0.75)
        model.bias.fill_(0.25)
    return model.state_dict()


def test_installed_security_build_verifies() -> None:
    from autocontext.accelerate_patch import require_patched_accelerate
    require_patched_accelerate()


def _save(path: Path, weights, safe: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if safe:
        from safetensors.torch import save_file
        save_file(weights, str(path), metadata={"format": "pt"})
    else:
        torch.save(weights, path)


def _load(checkpoint: Path, dispatch: bool = False):
    model = torch.nn.Linear(2, 1)
    loader = accelerate.load_checkpoint_and_dispatch if dispatch else accelerate.load_checkpoint_in_model
    loader(model, checkpoint=checkpoint, device_map={"": "cpu"})
    return model.state_dict()


@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize("sharded", [False, True])
@pytest.mark.parametrize("dispatch", [False, True])
def test_valid_checkpoint_forms_preserve_weights(tmp_path: Path, safe: bool, sharded: bool, dispatch: bool) -> None:
    weights = _weights()
    if sharded:
        suffix = "safetensors" if safe else "bin"
        first, second = f"nested/one.{suffix}", f"two.{suffix}"
        _save(tmp_path / first, {"weight": weights["weight"]}, safe)
        _save(tmp_path / second, {"bias": weights["bias"]}, safe)
        (tmp_path / "model.index.json").write_text(json.dumps({"weight_map": {"weight": first, "bias": second}}))
    else:
        _save(tmp_path / ("model.safetensors" if safe else "pytorch_model.bin"), weights, safe)
    loaded = _load(tmp_path, dispatch)
    for name in weights:
        assert torch.equal(loaded[name], weights[name])


@pytest.mark.parametrize("name", [
    "../outside.bin", "/tmp/outside.bin", "sub/../../outside.bin", "sub/../outside.bin",
    "C:/outside.bin", "C:outside.bin", "\\\\server\\share\\outside.bin", "sub\\outside.bin",
    "", ".", "nested//one.bin", None, 42, ["one.bin"],
])
def test_hostile_indexes_never_reach_tensor_loader(tmp_path: Path, monkeypatch, name) -> None:
    import accelerate.utils.modeling as modeling
    (tmp_path / "model.index.json").write_text(json.dumps({"weight_map": {"weight": name}}))
    def forbidden(*args, **kwargs):
        raise AssertionError("unsafe index reached tensor loading")
    monkeypatch.setattr(modeling, "load_state_dict", forbidden)
    with pytest.raises(ValueError):
        _load(tmp_path)


@pytest.mark.parametrize("directory_link", [False, True])
def test_symlink_escape_is_rejected(tmp_path: Path, directory_link: bool) -> None:
    root = tmp_path / "checkpoint"
    root.mkdir()
    outside = tmp_path / "outside"
    _save(outside / "weights.bin", _weights())
    if directory_link:
        (root / "linked").symlink_to(outside, target_is_directory=True)
        name = "linked/weights.bin"
    else:
        (root / "weights.bin").symlink_to(outside / "weights.bin")
        name = "weights.bin"
    (root / "model.index.json").write_text(json.dumps({"weight_map": {"weight": name}}))
    with pytest.raises(ValueError):
        _load(root)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file regression")
@pytest.mark.parametrize("index_fifo", [False, True])
def test_fifo_is_rejected_without_blocking(tmp_path: Path, index_fifo: bool) -> None:
    if index_fifo:
        os.mkfifo(tmp_path / "model.index.json")
    else:
        os.mkfifo(tmp_path / "weights.bin")
        (tmp_path / "model.index.json").write_text(json.dumps({"weight_map": {"weight": "weights.bin"}}))
    code = (
        "import sys, torch, accelerate\n"
        "try:\n"
        "    accelerate.load_checkpoint_in_model(torch.nn.Linear(2,1), sys.argv[1])\n"
        "except ValueError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('FIFO accepted')\n"
    )
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_hub_snapshot_blob_links_remain_compatible(tmp_path: Path) -> None:
    cache = tmp_path / "models--org--model"
    snapshot = cache / "snapshots" / ("a" * 40)
    snapshot.mkdir(parents=True)
    blob = cache / "blobs" / ("b" * 64)
    weights = _weights()
    _save(blob, weights)
    (snapshot / "weights.bin").symlink_to("../../blobs/" + blob.name)
    (snapshot / "model.index.json").write_text(json.dumps({"weight_map": {"weight": "weights.bin"}}))
    assert torch.equal(_load(snapshot)["weight"], weights["weight"])


def test_source_replacement_after_open_cannot_redirect_loading(tmp_path: Path, monkeypatch) -> None:
    import accelerate._checkpoint_security as security
    root = tmp_path / "checkpoint"
    root.mkdir()
    weights = _weights()
    original = root / "weights.bin"
    _save(original, weights)
    outside = tmp_path / "outside.bin"
    _save(outside, {"weight": torch.zeros_like(weights["weight"]), "bias": weights["bias"]})
    (root / "model.index.json").write_text(json.dumps({"weight_map": {"weight": "weights.bin"}}))
    copy = security.shutil.copyfileobj
    def replace_then_copy(source, destination, **kwargs):
        original.unlink()
        original.symlink_to(outside)
        return copy(source, destination, **kwargs)
    monkeypatch.setattr(security.shutil, "copyfileobj", replace_then_copy)
    assert torch.equal(_load(root)["weight"], weights["weight"])


def test_maintained_public_aliases_share_the_guard() -> None:
    import accelerate.big_modeling as big
    import accelerate.utils as utils
    import accelerate.utils.modeling as modeling
    assert accelerate.load_checkpoint_in_model is utils.load_checkpoint_in_model is modeling.load_checkpoint_in_model
    assert big.load_checkpoint_in_model is modeling.load_checkpoint_in_model
    assert modeling.load_checkpoint_in_model.__wrapped__.__name__ == "load_checkpoint_in_model"
