from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "docs" / "run-progress-report-parity-fixture.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_build_run_progress_report_matches_shared_fixture() -> None:
    from autocontext.analytics.progress_report import build_run_progress_report

    fixture = _fixture()
    report = build_run_progress_report(
        run_id=fixture["run_id"],
        events=fixture["events"],
        threshold=fixture["threshold"],
        pass_at_k_values=fixture["pass_at_k_values"],
        generated_at=fixture["generated_at"],
    )

    assert report.model_dump(mode="json") == fixture["expected_report"]


def test_run_progress_report_round_trips_from_shared_json() -> None:
    from autocontext.analytics.progress_report import RunProgressReport

    expected = _fixture()["expected_report"]
    assert RunProgressReport.model_validate(expected).model_dump(mode="json") == expected


def test_run_progress_report_rejects_schema_invalid_data() -> None:
    from autocontext.analytics.progress_report import RunProgressReport

    for payload in [
        {**_fixture()["expected_report"], "surprise": True},
        {**_fixture()["expected_report"], "run_id": ""},
        {**_fixture()["expected_report"], "generated_at": ""},
    ]:
        try:
            RunProgressReport.model_validate(payload)
        except ValueError:
            continue
        raise AssertionError("schema-invalid progress report was accepted")


def test_run_progress_report_uses_artifact_store_round_trip(tmp_path: Path) -> None:
    from autocontext.analytics.progress_report import RunProgressReport, build_run_progress_report
    from autocontext.storage.artifacts import ArtifactStore

    fixture = _fixture()
    report = build_run_progress_report(
        run_id=fixture["run_id"],
        events=fixture["events"],
        threshold=fixture["threshold"],
        pass_at_k_values=fixture["pass_at_k_values"],
        generated_at=fixture["generated_at"],
    )
    store = ArtifactStore(
        tmp_path / "runs",
        tmp_path / "knowledge",
        tmp_path / "skills",
        tmp_path / "claude-skills",
    )

    store.write_progress_report("grid_ctf", fixture["run_id"], report)
    loaded = store.read_progress_report("grid_ctf", fixture["run_id"])

    assert isinstance(loaded, RunProgressReport)
    assert loaded.to_dict() == fixture["expected_report"]
    assert "pass@4" in store.read_latest_progress_reports_markdown("grid_ctf")


def test_run_inspection_can_reference_progress_report() -> None:
    from autocontext.analytics.progress_report import RunProgressReport
    from autocontext.analytics.run_trace import ActorRef, RunTrace, TraceEvent
    from autocontext.analytics.timeline_inspector import StateInspector

    trace = RunTrace(
        trace_id="trace-progress",
        run_id="run-progress-1",
        generation_index=None,
        schema_version="1.0.0",
        events=[
            TraceEvent(
                event_id="e1",
                run_id="run-progress-1",
                generation_index=1,
                sequence_number=1,
                timestamp="2026-01-01T00:00:20Z",
                category="checkpoint",
                event_type="candidate_scored",
                actor=ActorRef(actor_type="system", actor_id="progress", actor_name="Progress"),
                resources=[],
                summary="candidate scored",
                detail={},
                parent_event_id=None,
                cause_event_ids=[],
                evidence_ids=[],
                severity="info",
                stage="gate",
                outcome="success",
                duration_ms=None,
            )
        ],
        causal_edges=[],
        created_at="2026-01-01T00:00:00Z",
        metadata={},
    )
    report = RunProgressReport.model_validate(_fixture()["expected_report"])

    inspection = StateInspector().inspect_run(trace, progress_report=report)

    assert inspection.progress_report is not None
    assert inspection.progress_report["run_id"] == "run-progress-1"
    assert inspection.progress_report["best_score"] == 0.83
    assert inspection.progress_report["pass_at_k"][-1]["passed"] is True
