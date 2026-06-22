from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from autocontext.training.autoresearch.opd_pressure import (
    OPD_PRESSURE_MODE_CODES,
    normalize_opd_pressure_mode,
    sampled_token_pressure_summary,
    selected_sample_mask,
)


def test_positive_mode_selects_only_teacher_advantaged_tokens() -> None:
    margins = [0.4, -0.2, 0.0, 1.1]

    assert selected_sample_mask(margins, "sample_positive") == [True, False, False, True]
    assert sampled_token_pressure_summary(margins, "sample_positive") == {
        "opd_positive_token_fraction": 0.5,
        "opd_negative_token_fraction": 0.25,
        "opd_mean_masked_loss": 0.75,
    }


def test_positive_mode_all_negative_batch_has_zero_masked_loss() -> None:
    summary = sampled_token_pressure_summary([-0.5, -0.25], "sample_positive")

    assert summary["opd_positive_token_fraction"] == 0.0
    assert summary["opd_negative_token_fraction"] == 1.0
    assert summary["opd_mean_masked_loss"] == 0.0


def test_positive_mode_all_positive_batch_keeps_every_token() -> None:
    summary = sampled_token_pressure_summary([0.25, 0.75], "sample_positive")

    assert selected_sample_mask([0.25, 0.75], "sample_positive") == [True, True]
    assert summary["opd_positive_token_fraction"] == 1.0
    assert summary["opd_negative_token_fraction"] == 0.0
    assert summary["opd_mean_masked_loss"] == 0.5


def test_reverse_negative_mode_keeps_both_pressure_directions() -> None:
    margins = [0.4, -0.2, 0.0]

    summary = sampled_token_pressure_summary(margins, "sample_positive_reverse_negative")

    assert selected_sample_mask(margins, "sample_positive_reverse_negative") == [True, True, False]
    assert summary["opd_mean_masked_loss"] == pytest.approx(0.3)


def test_run_training_forwards_pressure_mode_to_opd_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    train = importlib.import_module("autocontext.training.autoresearch.train")
    fake_opd = types.ModuleType("autocontext.training.autoresearch.on_policy_distill")
    captured: dict[str, Any] = {}

    def fake_run_on_policy_distillation(**kwargs: Any) -> dict[str, float]:
        captured.update(kwargs)
        return {
            "avg_score": 0.0,
            "valid_rate": 0.0,
            "training_seconds": 0.0,
            "peak_memory_mb": 0.0,
            "num_steps": 1.0,
            "num_params_m": 0.0,
            "depth": 0.0,
        }

    fake_opd.run_on_policy_distillation = fake_run_on_policy_distillation  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "autocontext.training.autoresearch.on_policy_distill", fake_opd)
    monkeypatch.setattr(train, "_preflight_backend_deps", lambda _backend: None)

    train.run_training(
        scenario_name="gsm8k",
        data_path=tmp_path / "data.jsonl",
        output_dir=tmp_path / "out",
        time_budget=1,
        memory_limit_mb=1024,
        backend="opd",
        opd_pressure_mode="sample_positive",
    )

    assert captured["opd_pressure_mode"] == "sample_positive"


def test_run_training_rejects_pressure_mode_for_incompatible_backends(tmp_path: Path) -> None:
    train = importlib.import_module("autocontext.training.autoresearch.train")

    with pytest.raises(NotImplementedError, match="--opd-pressure-mode"):
        train.run_training(
            scenario_name="gsm8k",
            data_path=tmp_path / "data.jsonl",
            output_dir=tmp_path / "out",
            time_budget=1,
            memory_limit_mb=1024,
            backend="mlx",
            opd_pressure_mode="sample_positive",
        )


def test_pressure_mode_normalization_and_codes_are_stable() -> None:
    assert normalize_opd_pressure_mode(" Sample_Positive ") == "sample_positive"
    assert OPD_PRESSURE_MODE_CODES == {
        "full_kl": 0.0,
        "sample_positive": 1.0,
        "sample_positive_reverse_negative": 2.0,
    }
    with pytest.raises(ValueError, match=r"full_kl\|sample_positive\|sample_positive_reverse_negative"):
        normalize_opd_pressure_mode("invented")
