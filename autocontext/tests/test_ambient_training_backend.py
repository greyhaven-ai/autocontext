"""tests for the ambient training-backend seam."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import autocontext.ambient.training_backend as tb
from autocontext.ambient.training_backend import TrainRequest, run_training, select_backend


def test_select_backend_returns_available_name(monkeypatch: Any) -> None:
    monkeypatch.setattr(tb, "_availability", lambda: {"mlxlm": False, "trl": True})
    assert select_backend("sft-distill") == "trl"


def test_select_backend_prefers_mlxlm_when_available(monkeypatch: Any) -> None:
    monkeypatch.setattr(tb, "_availability", lambda: {"mlxlm": True, "trl": True})
    assert select_backend("sft-distill") == "mlxlm"


def test_select_backend_none_when_nothing_available(monkeypatch: Any) -> None:
    monkeypatch.setattr(tb, "_availability", lambda: {"mlxlm": False, "trl": False})
    assert select_backend("sft-distill") is None


def test_run_training_dispatches_and_derives_gpu_hours(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> dict[str, float]:
        captured.update(kwargs)
        return {"avg_score": 0.8, "valid_rate": 1.0, "training_seconds": 3600.0, "num_records": 12.0}

    monkeypatch.setattr(tb, "_BACKEND_FNS", {"trl": fake_run})
    request = TrainRequest(
        scenario="grid_ctf",
        data_path=tmp_path / "ds.jsonl",
        output_dir=tmp_path / "out",
        base_model="tiny",
        time_budget_seconds=600,
        memory_limit_mb=4096,
    )

    outcome = run_training("trl", request)

    assert outcome.backend == "trl"
    assert outcome.metrics["avg_score"] == 0.8
    assert outcome.gpu_hours == 1.0  # 3600s / 3600
    assert captured["scenario_name"] == "grid_ctf"
    assert captured["data_path"] == request.data_path
    assert captured["base_model"] == "tiny"


def test_run_training_unknown_backend_raises(tmp_path: Path) -> None:
    request = TrainRequest("s", tmp_path / "d", tmp_path / "o", "m", 600, 4096)
    try:
        run_training("nonesuch", request)
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown backend")
