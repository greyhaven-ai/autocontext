from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocontext.server.cockpit_api import cockpit_router  # type: ignore[import-untyped]
from autocontext.storage.sqlite_store import SQLiteStore  # type: ignore[import-untyped]


def _build_cockpit_env(tmp_path: Path) -> dict[str, Any]:
    from autocontext.config.settings import AppSettings  # type: ignore[import-untyped]

    db_path = tmp_path / "autocontext.db"
    store = SQLiteStore(db_path)
    store.migrate(Path(__file__).resolve().parents[1] / "migrations")
    settings = AppSettings(
        db_path=db_path,
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
    )
    settings.runs_root.mkdir(parents=True, exist_ok=True)
    settings.knowledge_root.mkdir(parents=True, exist_ok=True)

    app = FastAPI()
    app.state.store = store
    app.state.app_settings = settings
    app.include_router(cockpit_router)
    return {"client": TestClient(app), "settings": settings, "store": store}


def _write_report(settings: Any, run_id: str) -> None:
    report_dir = settings.runs_root / run_id / "trace-findings"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "latest.json").write_text(
        json.dumps(
            {
                "reportId": "report-run-123",
                "traceId": "trace-run-123",
                "sourceHarness": "autocontext",
                "createdAt": "2026-06-01T12:00:00.000Z",
                "summary": "1 finding(s) across 1 category.",
                "metadata": {},
                "findings": [
                    {
                        "findingId": "finding-tool-1",
                        "category": "tool_call_failure",
                        "severity": "high",
                        "title": "Patch tool failed twice",
                        "description": "patch hunk did not apply",
                        "evidenceMessageIndexes": [1, 3],
                    }
                ],
                "failureMotifs": [
                    {
                        "motifId": "motif-tool",
                        "category": "tool_call_failure",
                        "occurrenceCount": 2,
                        "evidenceMessageIndexes": [1, 3],
                        "description": "patch tool failures repeated",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_proposal(settings: Any, run_id: str) -> None:
    proposal_dir = settings.runs_root / run_id / "harness-proposals"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    (proposal_dir / "01HX0000000000000000000683.json").write_text(
        json.dumps(
            {
                "id": "01HX0000000000000000000683",
                "status": "accepted",
                "findingIds": ["finding-tool-1"],
                "targetSurface": "prompt",
                "proposedEdit": {
                    "summary": "accepted proposal for trace finding",
                    "patches": [{"filePath": "prompt.txt", "operation": "modify", "unifiedDiff": "--- a\n+++ b\n"}],
                },
                "rollbackCriteria": ["heldout score regresses"],
                "decision": {
                    "status": "accepted",
                    "reason": "Accepted on heldout validation.",
                    "validation": {
                        "mode": "heldout",
                        "suiteId": "heldout-suite",
                        "evidenceRefs": ["runs/run-123/accepted.json"],
                    },
                    "decidedAt": "2026-06-01T12:10:00.000Z",
                },
            }
        ),
        encoding="utf-8",
    )


def test_cockpit_trace_gate_review_reads_report_and_gate_decisions(tmp_path: Path) -> None:
    cockpit_env = _build_cockpit_env(tmp_path)
    _write_report(cockpit_env["settings"], "run-123")
    _write_proposal(cockpit_env["settings"], "run-123")

    response = cockpit_env["client"].get("/api/cockpit/runs/run-123/trace-gates")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ready"
    assert body["findings"][0]["linked_proposal_ids"] == ["01HX0000000000000000000683"]
    assert body["gate_decisions"][0]["status"] == "accepted"
    assert body["gate_decisions"][0]["evidence_refs"] == [
        {
            "kind": "artifact",
            "ref": "runs/run-123/accepted.json",
            "label": "accepted.json",
            "href": "runs/run-123/accepted.json",
        }
    ]


def test_cockpit_trace_gate_review_handles_missing_and_invalid_run_ids(tmp_path: Path) -> None:
    cockpit_env = _build_cockpit_env(tmp_path)
    missing = cockpit_env["client"].get("/api/cockpit/runs/run-404/trace-gates")
    assert missing.status_code == 200
    assert missing.json()["state"] == "missing_report"

    invalid = cockpit_env["client"].get("/api/cockpit/runs/%20/trace-gates")
    assert invalid.status_code == 422
    assert "run_id is required" in invalid.json()["detail"]
