from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any


def _token_pressure() -> Any:
    return importlib.import_module("autocontext.training.autoresearch.token_pressure")


def test_token_pressure_report_summarizes_margins_without_token_text(tmp_path: Path) -> None:
    token_pressure = _token_pressure()
    report = token_pressure.build_token_pressure_report(
        [
            token_pressure.TokenPressureObservation(
                position=0,
                student_logprob=-2.0,
                teacher_logprob=-1.0,
                student_entropy=0.7,
                token_text="secret",
            ),
            token_pressure.TokenPressureObservation(
                position=1,
                student_logprob=-0.1,
                teacher_logprob=-1.1,
                student_entropy=0.2,
                token_text="token",
            ),
            token_pressure.TokenPressureObservation(
                position=1,
                student_logprob=-4.0,
                teacher_logprob=-0.5,
                student_entropy=0.3,
                token_text="spike",
            ),
        ],
        backend="opd",
        mode="opd",
        seed=7,
        response_lengths=[2, 3],
        shock_threshold=2.0,
    )

    assert report["schema_version"] == 1
    assert report["backend"] == "opd"
    assert report["mode"] == "opd"
    assert report["seed"] == 7
    assert report["token_count"] == 3
    assert report["positive_pressure_ratio"] == 2 / 3
    assert report["negative_pressure_ratio"] == 1 / 3
    assert report["mean_positive_margin"] == 2.25
    assert report["mean_negative_margin"] == -1.0
    assert report["mean_response_length"] == 2.5
    assert report["shock_spike_count"] == 1
    assert report["position_pressure"][1]["count"] == 2
    assert report["raw_token_text_persisted"] is False
    assert "secret" not in json.dumps(report)

    path = token_pressure.write_token_pressure_report(tmp_path / "pressure.json", report)
    assert json.loads(path.read_text(encoding="utf-8"))["token_count"] == 3


def test_token_pressure_report_debug_tokens_are_explicit_opt_in() -> None:
    token_pressure = _token_pressure()
    report = token_pressure.build_token_pressure_report(
        [
            token_pressure.TokenPressureObservation(
                position=0,
                student_logprob=-5.0,
                teacher_logprob=-1.0,
                token_text="debug-token",
            )
        ],
        backend="trl",
        mode="gkd",
        include_token_text=True,
        shock_threshold=1.0,
    )

    assert report["raw_token_text_persisted"] is True
    assert report["shock_spikes"][0]["token_text"] == "debug-token"


def test_bounded_diagnostic_inputs_caps_prompt_and_token_budget() -> None:
    token_pressure = _token_pressure()

    prompts, max_tokens = token_pressure.bounded_diagnostic_inputs(
        list(range(20)),
        512,
        remaining_seconds=1.0,
    )

    assert prompts == list(range(8))
    assert max_tokens == 64
    assert token_pressure.bounded_diagnostic_inputs([1, 2], 512, remaining_seconds=0.0) == ([], 0)


def test_compare_token_pressure_reports_orders_runs_for_ab_comparison() -> None:
    token_pressure = _token_pressure()
    comparison = token_pressure.compare_token_pressure_reports(
        [
            {"run_id": "neg", "positive_pressure_ratio": 0.25, "negative_pressure_ratio": 0.75, "shock_spike_count": 3},
            {"run_id": "pos", "positive_pressure_ratio": 0.75, "negative_pressure_ratio": 0.25, "shock_spike_count": 1},
        ]
    )

    assert comparison["run_count"] == 2
    assert comparison["mean_positive_pressure_ratio"] == 0.5
    assert comparison["highest_positive_pressure_run_id"] == "pos"
    assert comparison["highest_shock_run_id"] == "neg"
