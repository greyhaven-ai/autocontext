from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "docs" / "run-utilization-report-parity-fixture.json"


def _cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


def test_build_run_utilization_report_matches_shared_fixture() -> None:
    from autocontext.analytics.run_utilization_report import build_run_utilization_report

    for case in _cases():
        report = build_run_utilization_report(
            run_id=case["run_id"],
            events=case["events"],
            role_usage=case["role_usage"],
            generated_at=case["generated_at"],
        )

        assert report.to_dict() == case["expected_report"]


def test_run_utilization_report_round_trips_from_shared_json() -> None:
    from autocontext.analytics.run_utilization_report import RunUtilizationReport

    for case in _cases():
        expected = case["expected_report"]
        assert RunUtilizationReport.from_dict(expected).to_dict() == expected


def test_run_utilization_report_rejects_schema_invalid_data() -> None:
    from autocontext.analytics.run_utilization_report import RunUtilizationReport

    expected = _cases()[1]["expected_report"]
    missing_duration = {
        **expected,
        "window": {k: v for k, v in expected["window"].items() if k != "duration_seconds"},
    }
    missing_model_wait = {
        **expected,
        "token_utilization": {k: v for k, v in expected["token_utilization"].items() if k != "model_wait_seconds"},
    }
    negative_tokens = {
        **expected,
        "token_utilization": {**expected["token_utilization"], "input_tokens": -1},
    }
    negative_eval_count = {
        **expected,
        "evaluation_utilization": {**expected["evaluation_utilization"], "eval_count": -1},
    }
    for payload in [
        {**expected, "surprise": True},
        {**expected, "run_id": ""},
        {**expected, "generated_at": ""},
        missing_duration,
        missing_model_wait,
        negative_tokens,
        negative_eval_count,
    ]:
        try:
            RunUtilizationReport.from_dict(payload)
        except ValueError:
            continue
        raise AssertionError("schema-invalid utilization report was accepted")


def test_build_run_utilization_report_never_emits_negative_token_counts() -> None:
    from autocontext.analytics.run_utilization_report import build_run_utilization_report

    report = build_run_utilization_report(
        run_id="negative-token-run",
        generated_at="2026-06-16T12:00:00Z",
        events=[
            {
                "event_type": "evaluation_finished",
                "timestamp": "2026-06-16T12:00:01Z",
                "branch_id": "branch-a",
                "duration_seconds": 1,
                "verifier_passed": True,
            }
        ],
        role_usage=[
            {
                "timestamp": "2026-06-16T12:00:00Z",
                "branch_id": "branch-a",
                "input_tokens": -10,
                "output_tokens": -5,
            }
        ],
    )

    assert report.token_utilization.input_tokens == 0
    assert report.token_utilization.output_tokens == 0
    assert report.token_utilization.total_tokens == 0
    assert report.token_utilization.tokens_to_success == 0
