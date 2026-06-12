from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
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


def _valid_proposal_payload() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "id": "01HX0000000000000000000683",
        "status": "accepted",
        "findingIds": ["finding-tool-1"],
        "targetSurface": "prompt",
        "proposedEdit": {
            "summary": "accepted proposal for trace finding",
            "patches": [{"filePath": "prompt.txt", "operation": "modify", "unifiedDiff": "--- a\n+++ b\n"}],
        },
        "expectedImpact": {"qualityDelta": 0.08, "riskReduction": "fewer repeat tool failures"},
        "rollbackCriteria": ["heldout score regresses"],
        "provenance": {
            "authorType": "autocontext-run",
            "authorId": "run-123",
            "parentArtifactIds": [],
            "createdAt": "2026-06-01T12:05:00.000Z",
        },
        "decision": {
            "status": "accepted",
            "reason": "Accepted on heldout validation.",
            "validation": {
                "mode": "heldout",
                "suiteId": "heldout-suite",
                "evidenceRefs": ["runs/run-123/accepted.json"],
            },
            "promotionDecision": {
                "schemaVersion": "1.0",
                "pass": True,
                "recommendedTargetState": "canary",
                "deltas": {
                    "quality": {"baseline": 0.6, "candidate": 0.72, "delta": 0.12, "passed": True},
                    "cost": {
                        "baseline": {"tokensIn": 100, "tokensOut": 50},
                        "candidate": {"tokensIn": 110, "tokensOut": 55},
                        "delta": {"tokensIn": 10, "tokensOut": 5},
                        "passed": True,
                    },
                    "latency": {
                        "baseline": {"p50Ms": 10, "p95Ms": 20, "p99Ms": 30},
                        "candidate": {"p50Ms": 11, "p95Ms": 21, "p99Ms": 31},
                        "delta": {"p50Ms": 1, "p95Ms": 1, "p99Ms": 1},
                        "passed": True,
                    },
                    "safety": {"regressions": [], "passed": True},
                },
                "confidence": 0.9,
                "thresholds": {
                    "qualityMinDelta": 0.05,
                    "costMaxRelativeIncrease": 0.2,
                    "latencyMaxRelativeIncrease": 0.2,
                    "strongConfidenceMin": 0.9,
                    "moderateConfidenceMin": 0.7,
                    "strongQualityMultiplier": 2,
                },
                "reasoning": "Accepted on heldout validation.",
                "evaluatedAt": "2026-06-01T12:10:00.000Z",
            },
            "candidateArtifactId": "01HX0000000000000000000001",
            "candidateEvalRunId": "candidate-accepted",
            "baselineArtifactId": "01HX0000000000000000000002",
            "baselineEvalRunId": "baseline-accepted",
            "decidedAt": "2026-06-01T12:10:00.000Z",
        },
    }


def _write_proposal(settings: Any, run_id: str, payload: dict[str, Any] | None = None) -> None:
    proposal_dir = settings.runs_root / run_id / "harness-proposals"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    (proposal_dir / "01HX0000000000000000000683.json").write_text(
        json.dumps(payload or _valid_proposal_payload()),
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


def test_cockpit_trace_gate_review_rejects_invalid_proposal_json(tmp_path: Path) -> None:
    cockpit_env = _build_cockpit_env(tmp_path)
    _write_report(cockpit_env["settings"], "run-123")
    invalid_payload = _valid_proposal_payload()
    del invalid_payload["decision"]["promotionDecision"]
    _write_proposal(cockpit_env["settings"], "run-123", invalid_payload)

    response = cockpit_env["client"].get("/api/cockpit/runs/run-123/trace-gates")

    assert response.status_code == 500
    assert "Invalid HarnessChangeProposal" in response.json()["detail"]
    assert "promotionDecision" in response.json()["detail"]


@pytest.mark.parametrize(
    ("field_path", "expected_detail"),
    [
        (("provenance", "createdAt"), "provenance.createdAt"),
        (("decision", "decidedAt"), "decision.decidedAt"),
        (("decision", "promotionDecision", "evaluatedAt"), "decision.promotionDecision.evaluatedAt"),
    ],
)
def test_cockpit_trace_gate_review_rejects_non_schema_timestamps(
    tmp_path: Path,
    field_path: tuple[str, ...],
    expected_detail: str,
) -> None:
    cockpit_env = _build_cockpit_env(tmp_path)
    _write_report(cockpit_env["settings"], "run-123")
    invalid_payload = deepcopy(_valid_proposal_payload())
    target: dict[str, Any] = invalid_payload
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = "not-a-date"
    _write_proposal(cockpit_env["settings"], "run-123", invalid_payload)

    response = cockpit_env["client"].get("/api/cockpit/runs/run-123/trace-gates")

    assert response.status_code == 500
    assert "Invalid HarnessChangeProposal" in response.json()["detail"]
    assert expected_detail in response.json()["detail"]


def test_cockpit_trace_gate_review_handles_missing_and_invalid_run_ids(tmp_path: Path) -> None:
    cockpit_env = _build_cockpit_env(tmp_path)
    missing = cockpit_env["client"].get("/api/cockpit/runs/run-404/trace-gates")
    assert missing.status_code == 200
    assert missing.json()["state"] == "missing_report"

    invalid = cockpit_env["client"].get("/api/cockpit/runs/%20/trace-gates")
    assert invalid.status_code == 422
    assert "run_id is required" in invalid.json()["detail"]
