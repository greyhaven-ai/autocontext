"""Tests for TrendAwareGate integration into GenerationRunner.

Verifies that when AUTOCONTEXT_BACKPRESSURE_MODE=trend:
- TrendAwareGate is used instead of BackpressureGate
- Score history is accumulated across generations and passed to the gate
- scenario.custom_backpressure() is called and metrics passed to gate
- Default mode (simple) behavior is unchanged
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.backpressure import BackpressureGate, TrendAwareGate
from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner


def _make_settings(tmp_path: Path, **overrides: Any) -> AppSettings:
    defaults: dict[str, Any] = {
        "db_path": tmp_path / "runs" / "autocontext.sqlite3",
        "runs_root": tmp_path / "runs",
        "knowledge_root": tmp_path / "knowledge",
        "skills_root": tmp_path / "skills",
        "event_stream_path": tmp_path / "runs" / "events.ndjson",
        "seed_base": 3000,
        "agent_provider": "deterministic",
        "matches_per_generation": 2,
        "retry_backoff_seconds": 0.0,
    }
    defaults.update(overrides)
    return AppSettings(**defaults)


def _make_runner(settings: AppSettings) -> GenerationRunner:
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)
    return runner


def test_trend_mode_uses_trend_aware_gate(tmp_path: Path) -> None:
    """When backpressure_mode='trend', runner should use TrendAwareGate."""
    settings = _make_settings(tmp_path, backpressure_mode="trend")
    runner = _make_runner(settings)
    assert isinstance(runner.gate, TrendAwareGate)


def test_simple_mode_uses_backpressure_gate(tmp_path: Path) -> None:
    """When backpressure_mode='simple' (default), runner should use BackpressureGate."""
    settings = _make_settings(tmp_path, backpressure_mode="simple")
    runner = _make_runner(settings)
    assert isinstance(runner.gate, BackpressureGate)


def test_trend_gate_receives_score_history(tmp_path: Path) -> None:
    """Score history should be accumulated and passed to TrendAwareGate across generations."""
    settings = _make_settings(tmp_path, backpressure_mode="trend")
    runner = _make_runner(settings)

    # Spy on gate.evaluate to capture history argument
    calls: list[dict[str, Any]] = []
    original_evaluate = runner.gate.evaluate

    def capturing_evaluate(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        return original_evaluate(*args, **kwargs)

    runner.gate.evaluate = capturing_evaluate  # type: ignore[assignment]

    runner.run(scenario_name="grid_ctf", generations=3, run_id="history_test")

    # Gen 1 should have no history (or empty). Gen 2+ should have accumulated history.
    assert len(calls) >= 3, f"Expected at least 3 gate evaluations, got {len(calls)}"

    # Gen 1: history should be None or have empty scores
    gen1_history = calls[0]["kwargs"].get("history")
    assert gen1_history is None or len(gen1_history.scores) == 0

    # Gen 2: history should have 1 score (from gen 1)
    gen2_history = calls[1]["kwargs"].get("history")
    assert gen2_history is not None
    assert len(gen2_history.scores) == 1

    # Gen 3: history should have 2 scores (from gens 1 and 2)
    gen3_history = calls[2]["kwargs"].get("history")
    assert gen3_history is not None
    assert len(gen3_history.scores) == 2


def test_trend_gate_receives_custom_metrics(tmp_path: Path) -> None:
    """custom_backpressure() metrics from scenario should be passed to TrendAwareGate."""
    settings = _make_settings(tmp_path, backpressure_mode="trend")
    runner = _make_runner(settings)

    calls: list[dict[str, Any]] = []
    original_evaluate = runner.gate.evaluate

    def capturing_evaluate(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        return original_evaluate(*args, **kwargs)

    runner.gate.evaluate = capturing_evaluate  # type: ignore[assignment]

    runner.run(scenario_name="grid_ctf", generations=1, run_id="metrics_test")

    assert len(calls) >= 1
    custom_metrics = calls[0]["kwargs"].get("custom_metrics")
    # custom_backpressure() default returns {"score": result.score}
    assert custom_metrics is not None
    assert "score" in custom_metrics


def test_simple_mode_full_run_unchanged(tmp_path: Path) -> None:
    """Full run with simple mode should work identically to before — no history or custom_metrics passed."""
    settings = _make_settings(tmp_path, backpressure_mode="simple")
    runner = _make_runner(settings)

    summary = runner.run(scenario_name="grid_ctf", generations=2, run_id="simple_test")
    assert summary.generations_executed == 2
    assert summary.best_score > 0

    # Verify metrics are persisted correctly
    metrics_path = tmp_path / "runs" / "simple_test" / "generations" / "gen_1" / "metrics.json"
    assert metrics_path.exists()
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["generation_index"] == 1


def test_trend_mode_gate_history_includes_decisions(tmp_path: Path) -> None:
    """Score history gate_decisions should accumulate gate decisions from prior generations."""
    settings = _make_settings(tmp_path, backpressure_mode="trend")
    runner = _make_runner(settings)

    calls: list[dict[str, Any]] = []
    original_evaluate = runner.gate.evaluate

    def capturing_evaluate(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        return original_evaluate(*args, **kwargs)

    runner.gate.evaluate = capturing_evaluate  # type: ignore[assignment]

    runner.run(scenario_name="grid_ctf", generations=3, run_id="decisions_test")

    # Gen 3 should have 2 gate decisions in history (from gens 1 and 2)
    gen3_history = calls[2]["kwargs"].get("history")
    assert gen3_history is not None
    assert len(gen3_history.gate_decisions) == 2
    # Each decision should be a valid gate decision string
    for d in gen3_history.gate_decisions:
        assert d in ("advance", "retry", "rollback")
