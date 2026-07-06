"""tests for the ambient training-backend seam."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import autocontext.ambient.training_backend as tb
from autocontext.ambient.training_backend import BackendEntry, TrainRequest, run_training, select_backend


def _request(tmp_path: Path) -> TrainRequest:
    return TrainRequest(
        scenario="grid_ctf",
        data_path=tmp_path / "ds.jsonl",
        output_dir=tmp_path / "out",
        base_model="tiny",
        time_budget_seconds=600,
        memory_limit_mb=4096,
    )


def test_select_backend_returns_available_name(monkeypatch: Any) -> None:
    monkeypatch.setattr(tb, "_availability", lambda: {"mlxlm": True})
    assert select_backend("sft-distill") == "mlxlm"


def test_select_backend_none_when_unavailable(monkeypatch: Any) -> None:
    # the linux/cpu-ci path: no mlx runtime, so the train stage cleanly no-ops
    monkeypatch.setattr(tb, "_availability", lambda: {"mlxlm": False})
    assert select_backend("sft-distill") is None


def test_select_backend_unknown_method_is_none(monkeypatch: Any) -> None:
    monkeypatch.setattr(tb, "_availability", lambda: {"mlxlm": True})
    assert select_backend("rlvr-experimental") is None  # no backend wired for it yet
    assert select_backend("bogus") is None


def test_select_backend_prefers_mlxlm_over_sft(monkeypatch: Any) -> None:
    # both installed (a mac with trl too): mlxlm wins because it is first in the preference tuple.
    monkeypatch.setattr(tb, "_availability", lambda: {"mlxlm": True, "sft": True})
    assert select_backend("sft-distill") == "mlxlm"


def test_select_backend_falls_back_to_sft_on_linux(monkeypatch: Any) -> None:
    # the linux/cuda path: no mlx runtime but trl+torch present, so sft is selected.
    monkeypatch.setattr(tb, "_availability", lambda: {"mlxlm": False, "sft": True})
    assert select_backend("sft-distill") == "sft"


def test_select_backend_none_when_neither_available(monkeypatch: Any) -> None:
    monkeypatch.setattr(tb, "_availability", lambda: {"mlxlm": False, "sft": False})
    assert select_backend("sft-distill") is None


def test_sft_adapter_maps_request_including_whole_run_deadline(tmp_path: Path) -> None:
    # the sft adapter carries every run_sft_training kwarg plus the whole-run deadline: the
    # deadline is the wall-clock ceiling = time_budget_seconds, in seconds and as a float.
    request = _request(tmp_path)
    kwargs = tb._BACKEND["sft"].adapt(request)
    assert kwargs == {
        "scenario_name": "grid_ctf",
        "data_path": request.data_path,
        "output_dir": request.output_dir,
        "base_model": "tiny",
        "time_budget": request.time_budget_seconds,
        "memory_limit_mb": request.memory_limit_mb,
        "deadline_seconds": 600.0,
    }
    assert kwargs["deadline_seconds"] == float(request.time_budget_seconds)
    assert isinstance(kwargs["deadline_seconds"], float)


def test_sft_is_deadline_capable_and_mlxlm_is_not() -> None:
    # only the sft backend enforces a real in-run wall-clock deadline, so only it is marked
    # deadline-capable; the train stage reads this set to pick the budget reservation.
    assert "sft" in tb._DEADLINE_CAPABLE
    assert "mlxlm" not in tb._DEADLINE_CAPABLE


def test_run_training_dispatches_and_derives_gpu_hours(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> dict[str, float]:
        captured.update(kwargs)
        return {"avg_score": 0.8, "valid_rate": 1.0, "training_seconds": 3600.0, "num_records": 12.0}

    # monkeypatch a whole registry entry: the fn is swapped for capture, the real
    # mlxlm adapter maps the TrainRequest to that backend's exact kwargs.
    real_adapt = tb._BACKEND["mlxlm"].adapt
    monkeypatch.setitem(tb._BACKEND, "mlxlm", BackendEntry(fn=fake_run, adapt=real_adapt))
    request = _request(tmp_path)

    outcome = run_training("mlxlm", request)

    assert outcome.backend == "mlxlm"
    assert outcome.metrics["avg_score"] == 0.8
    assert outcome.gpu_hours == 1.0  # 3600s / 3600
    assert captured["scenario_name"] == "grid_ctf"
    assert captured["data_path"] == request.data_path
    assert captured["base_model"] == "tiny"
    assert captured["output_dir"] == request.output_dir
    assert captured["time_budget"] == request.time_budget_seconds
    assert captured["memory_limit_mb"] == request.memory_limit_mb
    assert captured["dedupe"] is True  # train-time dedupe honors the curate crash-window mitigation


def test_mlxlm_adapter_maps_request_to_exact_kwargs(tmp_path: Path) -> None:
    # the adapter is the seam guarding a second backend from foreign kwargs: it must
    # produce exactly the mlxlm kwarg set, no more, no less.
    request = _request(tmp_path)
    kwargs = tb._BACKEND["mlxlm"].adapt(request)
    assert kwargs == {
        "scenario_name": "grid_ctf",
        "data_path": request.data_path,
        "output_dir": request.output_dir,
        "time_budget": request.time_budget_seconds,
        "memory_limit_mb": request.memory_limit_mb,
        "base_model": "tiny",
        "dedupe": True,
    }


def test_run_training_unknown_backend_raises(tmp_path: Path) -> None:
    request = _request(tmp_path)
    try:
        run_training("nonesuch", request)
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown backend")
