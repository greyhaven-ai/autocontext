from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.config.settings import AppSettings


def _write_events(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _event(seq: int, event: str, payload: dict) -> dict:
    return {
        "ts": f"2026-04-30T00:00:{seq:02d}+00:00",
        "v": 1,
        "seq": seq,
        "channel": "generation",
        "event": event,
        "payload": {"run_id": "run-1", "generation": 1, **payload},
    }


def _settings(tmp_path: Path, events_path: Path) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=events_path,
        agent_provider="deterministic",
    )


def test_events_to_trace_maps_runner_event_contract(tmp_path: Path) -> None:
    from autocontext.analytics.events_to_trace import collect_run_ids, events_to_trace

    rows = [
        _event(1, "run_started", {"scenario": "grid_ctf"}),
        _event(2, "agents_started", {}),
        _event(3, "role_event", {"role": "competitor", "status": "started", "model": "m"}),
        _event(4, "role_completed", {"role": "competitor", "status": "completed"}),
        _event(5, "tournament_started", {}),
        _event(6, "match_completed", {"passed_validation": True}),
        _event(7, "tournament_completed", {}),
        _event(8, "staged_validation_started", {}),
        _event(9, "staged_validation_completed", {"status": "passed"}),
        _event(10, "gate_decided", {"gate_decision": "advance"}),
        _event(11, "analyst_feedback_rated", {}),
        _event(12, "generation_completed", {}),
        _event(13, "generation_timing", {"duration_ms": 42}),
        _event(14, "holdout_evaluated", {}),
        _event(15, "curator_started", {}),
        _event(16, "curator_completed", {}),
        _event(17, "startup_verification", {"status": "passed"}),
        {"ts": "2026-04-30T00:01:00+00:00", "v": 1, "seq": 18, "event": "run_started", "payload": {"run_id": "run-2"}},
    ]
    events_path = tmp_path / "runs" / "events.ndjson"
    _write_events(events_path, rows)

    assert collect_run_ids(events_path) == ["run-1", "run-2"]
    trace = events_to_trace(events_path, "run-1")
    by_type = {event.event_type: event for event in trace.events}

    assert trace.run_id == "run-1"
    assert len(trace.events) == 17
    assert len(trace.causal_edges) == 16
    assert by_type["run_started"].category == "checkpoint"
    assert by_type["run_started"].stage == "init"
    assert by_type["role_event"].category == "action"
    assert by_type["role_event"].stage == "compete"
    assert by_type["match_completed"].category == "validation"
    assert by_type["match_completed"].stage == "match"
    assert by_type["match_completed"].outcome == "passed"
    assert by_type["staged_validation_completed"].stage == "gate"
    assert by_type["gate_decided"].outcome == "advance"
    assert by_type["analyst_feedback_rated"].category == "observation"
    assert by_type["generation_timing"].duration_ms == 42
    assert by_type["curator_completed"].stage == "curate"
    assert by_type["startup_verification"].stage == "init"


def test_analytics_rebuild_traces_cli_writes_run_local_trace(tmp_path: Path) -> None:
    events_path = tmp_path / "runs" / "events.ndjson"
    _write_events(events_path, [_event(1, "run_started", {"scenario": "grid_ctf"})])
    settings = _settings(tmp_path, events_path)

    runner = CliRunner()
    with patch("autocontext.cli.load_settings", return_value=settings):
        result = runner.invoke(
            app,
            ["analytics", "rebuild-traces", "--events", str(events_path), "--run-id", "run-1", "--json"],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    trace_path = tmp_path / "runs" / "run-1" / "traces" / "trace-run-1.json"
    analytics_trace_path = tmp_path / "knowledge" / "analytics" / "traces" / "trace-run-1.json"
    assert payload["status"] == "completed"
    assert payload["rebuilt"][0]["path"] == str(trace_path)
    assert json.loads(trace_path.read_text(encoding="utf-8"))["run_id"] == "run-1"
    assert json.loads(analytics_trace_path.read_text(encoding="utf-8"))["run_id"] == "run-1"


def test_analytics_rebuild_traces_cli_rejects_missing_run_id(tmp_path: Path) -> None:
    events_path = tmp_path / "runs" / "events.ndjson"
    _write_events(events_path, [_event(1, "run_started", {"scenario": "grid_ctf"})])
    settings = _settings(tmp_path, events_path)

    runner = CliRunner()
    with patch("autocontext.cli.load_settings", return_value=settings):
        result = runner.invoke(
            app,
            ["analytics", "rebuild-traces", "--events", str(events_path), "--run-id", "run-missing", "--json"],
        )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert "No events found for run id" in payload["error"]
    assert not (tmp_path / "runs" / "run-missing" / "traces" / "trace-run-missing.json").exists()


def test_analytics_rebuild_traces_cli_rejects_run_id_escape(tmp_path: Path) -> None:
    events_path = tmp_path / "runs" / "events.ndjson"
    _write_events(events_path, [_event(1, "run_started", {"scenario": "grid_ctf", "run_id": "../outside"})])
    settings = _settings(tmp_path, events_path)

    runner = CliRunner()
    with patch("autocontext.cli.load_settings", return_value=settings):
        result = runner.invoke(
            app,
            ["analytics", "rebuild-traces", "--events", str(events_path), "--run-id", "../outside", "--json"],
        )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert "escapes runs root" in payload["error"]
    assert not (tmp_path / "outside" / "traces" / "trace-../outside.json").exists()


def test_analytics_context_selection_cli_reports_run_summary(tmp_path: Path) -> None:
    from autocontext.knowledge.context_selection import (
        ContextSelectionCandidate,
        ContextSelectionDecision,
    )
    from autocontext.storage.artifacts import ArtifactStore
    from autocontext.storage.context_selection_store import persist_context_selection_decision

    events_path = tmp_path / "runs" / "events.ndjson"
    settings = _settings(tmp_path, events_path)
    artifacts = ArtifactStore(
        settings.runs_root,
        settings.knowledge_root,
        settings.skills_root,
        settings.claude_skills_path,
    )
    persist_context_selection_decision(
        artifacts,
        ContextSelectionDecision(
            run_id="run-1",
            scenario_name="grid_ctf",
            generation=1,
            stage="generation_prompt_context",
            candidates=(
                ContextSelectionCandidate.from_contents(
                    artifact_id="playbook",
                    artifact_type="prompt_component",
                    source="prompt_assembly",
                    candidate_content="abcd",
                    selected_content="abcd",
                    selection_reason="retained",
                ),
            ),
        ),
    )

    runner = CliRunner()
    with patch("autocontext.cli.load_settings", return_value=settings):
        result = runner.invoke(app, ["analytics", "context-selection", "--run-id", "run-1", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "completed"
    assert payload["run_id"] == "run-1"
    assert payload["diagnostics"] == []
    assert payload["summary"]["selected_token_estimate"] == 1
    assert payload["telemetry_cards"][0]["key"] == "selected_context"


def test_analytics_context_selection_cli_rejects_missing_artifacts(tmp_path: Path) -> None:
    events_path = tmp_path / "runs" / "events.ndjson"
    settings = _settings(tmp_path, events_path)

    runner = CliRunner()
    with patch("autocontext.cli.load_settings", return_value=settings):
        result = runner.invoke(app, ["analytics", "context-selection", "--run-id", "run-missing", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert "No context selection artifacts found" in payload["error"]
