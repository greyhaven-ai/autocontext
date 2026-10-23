"""Installed CUDA builds must retain the reviewed security code and bundled artifact."""
from __future__ import annotations

import json
import types
from unittest.mock import MagicMock

import pytest

import autocontext.accelerate_patch as patch


def test_altered_bundled_wheel_is_rejected(tmp_path) -> None:
    wheel, manifest = patch.verified_bundle()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / wheel.name).write_bytes(wheel.read_bytes() + b"tampered")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        patch.verified_bundle(tmp_path)


def test_stock_accelerate_is_rejected_before_model_loading(monkeypatch) -> None:
    import sys

    from autocontext.providers.sft_torch_provider import SftTorchProvider
    models = MagicMock()
    tokenizer = MagicMock()
    monkeypatch.setitem(sys.modules, "torch", types.ModuleType("torch"))
    transformers = types.ModuleType("transformers")
    transformers.AutoModelForCausalLM = models
    transformers.AutoTokenizer = tokenizer
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setattr(patch.metadata, "distribution", lambda name: types.SimpleNamespace(version="1.15.0"))
    with pytest.raises(RuntimeError, match="maintained Accelerate security build"):
        SftTorchProvider("operator/model")
    models.from_pretrained.assert_not_called()
    tokenizer.from_pretrained.assert_not_called()


def test_installed_code_tampering_is_rejected(monkeypatch, tmp_path) -> None:
    _, manifest = patch.verified_bundle()
    distribution = types.SimpleNamespace(version=manifest["version"], locate_file=lambda name: tmp_path / name)
    monkeypatch.setattr(patch.metadata, "distribution", lambda name: distribution)
    with pytest.raises(RuntimeError, match="maintained Accelerate security build"):
        patch.require_patched_accelerate()
