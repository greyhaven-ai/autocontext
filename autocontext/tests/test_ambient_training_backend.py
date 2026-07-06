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
    # deadline-capable; the train stage reads this predicate to pick the budget reservation.
    assert tb.is_deadline_capable("sft") is True
    assert tb.is_deadline_capable("mlxlm") is False


def test_every_deadline_capable_backend_adapter_emits_deadline(tmp_path: Path) -> None:
    # the coupling invariant: a backend marked deadline-capable gets the exact-ceiling reservation,
    # which is only safe if its adapter actually passes a deadline to the backend fn. enforce that
    # every deadline-capable backend's adapter emits "deadline_seconds" so a future backend cannot be
    # marked capable (and reserved the tight ceiling) without a deadline that would let it overrun.
    request = _request(tmp_path)
    capable = [name for name in tb._BACKEND if tb.is_deadline_capable(name)]
    assert capable, "expected at least one deadline-capable backend"
    for name in capable:
        kwargs = tb._BACKEND[name].adapt(request)
        assert "deadline_seconds" in kwargs, f"deadline-capable backend {name!r} must emit deadline_seconds"


def test_non_deadline_capable_backend_adapter_omits_deadline(tmp_path: Path) -> None:
    # the inverse: a backend WITHOUT an in-run deadline (mlxlm) must not emit deadline_seconds; it is
    # reserved the conservative assess envelope precisely because it cannot stop itself at the ceiling.
    request = _request(tmp_path)
    for name in tb._BACKEND:
        if not tb.is_deadline_capable(name):
            kwargs = tb._BACKEND[name].adapt(request)
            assert "deadline_seconds" not in kwargs, f"non-deadline-capable backend {name!r} must not emit deadline_seconds"


def test_select_backend_none_when_sft_deps_incomplete(monkeypatch: Any) -> None:
    # end-to-end through the real _availability (not mocked): a box with trl+torch but datasets
    # (and mlx_lm) missing must NOT select sft, because is_available now requires all five sft deps.
    # Otherwise select_backend would pick sft and the run would preflight-error (a breaker-tripping
    # train failure) instead of cleanly returning None -> train_no_backend.
    present = {"trl", "torch", "transformers", "peft"}  # datasets + mlx_lm absent
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object() if name in present else None)
    assert select_backend("sft-distill") is None


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
