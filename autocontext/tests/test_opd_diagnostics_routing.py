from __future__ import annotations

import importlib
import sys
import types


def _summary() -> dict[str, float]:
    return {
        "avg_score": 0.4,
        "valid_rate": 1.0,
        "training_seconds": 0.0,
        "peak_memory_mb": 0.0,
        "num_steps": 0.0,
        "num_params_m": 0.0,
        "depth": 0.0,
    }


def test_run_training_forwards_opd_diagnostics_to_opd_backend(monkeypatch, tmp_path):
    train_mod = importlib.import_module("autocontext.training.autoresearch.train")
    captured: dict = {}
    stub = types.ModuleType("autocontext.training.autoresearch.on_policy_distill")
    stub.__dict__["run_on_policy_distillation"] = lambda **kw: captured.update(kw) or _summary()
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
    monkeypatch.setattr(train_mod, "_preflight_backend_deps", lambda backend: None)

    train_mod.run_training(
        scenario_name="grid_ctf",
        data_path=tmp_path / "d.jsonl",
        output_dir=tmp_path / "o",
        time_budget=10,
        memory_limit_mb=1024,
        backend="opd",
        opd_diagnostics=True,
        opd_diagnostics_debug_tokens=True,
    )

    assert captured["opd_diagnostics"] is True
    assert captured["opd_diagnostics_debug_tokens"] is True


def test_run_training_uses_opd_diagnostics_env_mirror(monkeypatch, tmp_path):
    train_mod = importlib.import_module("autocontext.training.autoresearch.train")
    captured: dict = {}
    stub = types.ModuleType("autocontext.training.autoresearch.on_policy_distill")
    stub.__dict__["run_on_policy_distillation"] = lambda **kw: captured.update(kw) or _summary()
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
    monkeypatch.setattr(train_mod, "_preflight_backend_deps", lambda backend: None)
    monkeypatch.setenv("AUTOCONTEXT_OPD_DIAGNOSTICS", "true")

    train_mod.run_training(
        scenario_name="grid_ctf",
        data_path=tmp_path / "d.jsonl",
        output_dir=tmp_path / "o",
        time_budget=10,
        memory_limit_mb=1024,
        backend="opd",
    )

    assert captured["opd_diagnostics"] is True


def test_run_training_forwards_opd_diagnostics_to_trl_gkd(monkeypatch, tmp_path):
    train_mod = importlib.import_module("autocontext.training.autoresearch.train")
    trl_mod = importlib.import_module("autocontext.training.autoresearch.trl_backend")
    captured: dict = {}
    monkeypatch.setattr(train_mod, "_preflight_backend_deps", lambda backend: None)
    monkeypatch.setattr(
        trl_mod,
        "run_trl_training",
        lambda **kw: captured.update(kw) or {"avg_score": 0.0, "valid_rate": 1.0},
    )

    train_mod.run_training(
        scenario_name="grid_ctf",
        data_path=tmp_path / "d.jsonl",
        output_dir=tmp_path / "o",
        time_budget=10,
        memory_limit_mb=1024,
        backend="trl",
        trl_mode="gkd",
        opd_diagnostics=True,
    )

    assert captured["opd_diagnostics"] is True
