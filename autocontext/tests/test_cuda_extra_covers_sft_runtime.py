"""The `cuda` optional extra must install everything the sft path imports.

Regression guard for the packaging gap where `autocontext[cuda]` shipped only torch, so the sft
training backend (trl/transformers/peft/torch/datasets) and the torch/peft serving provider
(transformers/peft/accelerate) hit ModuleNotFoundError and silently fell back to the frontier client.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from autocontext.training.autoresearch.sft_backend import _SFT_RUNTIME_DEPS

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _extra_package_names(extra: str) -> set[str]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    specs = data["project"]["optional-dependencies"][extra]
    # strip version specifiers and extras markers to just the distribution name
    return {re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0].strip().lower() for spec in specs}


def test_cuda_extra_covers_sft_training_runtime() -> None:
    cuda = _extra_package_names("cuda")
    missing = {dep.lower() for dep in _SFT_RUNTIME_DEPS} - cuda
    assert missing == set(), f"cuda extra is missing sft training runtime deps: {sorted(missing)}"


def test_cuda_extra_covers_sft_serving_runtime() -> None:
    # SftTorchProvider loads a transformers base model + a peft adapter, with accelerate for placement.
    cuda = _extra_package_names("cuda")
    required = {"torch", "transformers", "peft", "accelerate"}
    missing = required - cuda
    assert missing == set(), f"cuda extra is missing sft serving runtime deps: {sorted(missing)}"
