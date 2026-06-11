from __future__ import annotations

from autocontext.analytics.cross_runtime_trace_findings import CrossRuntimeTraceFindingReport  # type: ignore[import-untyped]
from autocontext.analytics.trace_gate_operator_view import (  # type: ignore[import-untyped]
    build_trace_gate_operator_view,
    render_trace_gate_operator_view_lines,
)

_REPORT_DATA = {
    "reportId": "report-run-123",
    "traceId": "trace-run-123",
    "sourceHarness": "autocontext",
    "createdAt": "2026-06-01T12:00:00.000Z",
    "summary": "2 finding(s) across 1 category.",
    "metadata": {},
    "findings": [
        {
            "findingId": "finding-tool-1",
            "category": "tool_call_failure",
            "severity": "high",
            "title": "Patch tool failed twice",
            "description": "patch hunk did not apply",
            "evidenceMessageIndexes": [1, 3],
        },
        {
            "findingId": "finding-score-1",
            "category": "low_outcome_score",
            "severity": "medium",
            "title": "Outcome stayed below threshold",
            "description": "score=0.32",
            "evidenceMessageIndexes": [],
        },
    ],
    "failureMotifs": [
        {
            "motifId": "motif-tool",
            "category": "tool_call_failure",
            "occurrenceCount": 2,
            "evidenceMessageIndexes": [1, 3],
            "description": "patch tool failures repeated",
        },
    ],
}


def _proposal(suffix: str, status: str, reason: str = "") -> dict[str, object]:
    return {
        "schemaVersion": "1.0",
        "id": f"01HX0000000000000000000{suffix}",
        "status": status,
        "findingIds": ["finding-tool-1"],
        "targetSurface": "verifier-rubric" if suffix == "683" else "prompt",
        "proposedEdit": {
            "summary": f"{status} proposal for trace finding",
            "patches": [
                {
                    "filePath": "agents/grid_ctf/prompts/competitor.txt",
                    "operation": "modify",
                    "unifiedDiff": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
                }
            ],
        },
        "expectedImpact": {"qualityDelta": 0.08, "riskReduction": "fewer repeat tool failures"},
        "rollbackCriteria": ["heldout score regresses"],
        "provenance": {
            "authorType": "autocontext-run",
            "authorId": "run-123",
            "parentArtifactIds": [],
            "createdAt": "2026-06-01T12:05:00.000Z",
        },
        **(
            {}
            if status == "proposed"
            else {
                "decision": {
                    "status": status,
                    "reason": reason,
                    "validation": {
                        "mode": "dev" if status == "inconclusive" else "heldout",
                        "suiteId": "heldout-suite",
                        "evidenceRefs": [] if status == "inconclusive" else [f"runs/run-123/{status}.json"],
                    },
                    "decidedAt": "2026-06-01T12:10:00.000Z",
                }
            }
        ),
    }


def test_build_trace_gate_operator_view_surfaces_findings_proposals_and_gates() -> None:
    report = CrossRuntimeTraceFindingReport.model_validate(_REPORT_DATA)
    view = build_trace_gate_operator_view(
        run_id="run-123",
        report=report,
        proposals=[
            _proposal("681", "accepted", "Accepted on heldout validation."),
            _proposal("682", "rejected", "Rejected on heldout validation."),
            _proposal("683", "inconclusive", "Dev-only validation is not enough."),
        ],
    )

    assert view["schema_version"] == "1"
    assert view["run_id"] == "run-123"
    assert view["state"] == "ready"
    assert view["report"] == {
        "report_id": "report-run-123",
        "trace_id": "trace-run-123",
        "source_harness": "autocontext",
        "created_at": "2026-06-01T12:00:00.000Z",
        "finding_count": 2,
        "failure_mode_count": 1,
    }
    assert view["findings"][0]["linked_proposal_ids"] == [
        "01HX0000000000000000000681",
        "01HX0000000000000000000682",
        "01HX0000000000000000000683",
    ]
    assert view["findings"][0]["evidence_refs"] == [
        {"kind": "trace_message", "ref": "msg:1", "label": "msg #1", "href": "#msg-1"},
        {"kind": "trace_message", "ref": "msg:3", "label": "msg #3", "href": "#msg-3"},
    ]
    assert [item["status"] for item in view["gate_decisions"]] == ["accepted", "rejected", "inconclusive"]
    assert view["gate_decisions"][0]["evidence_refs"] == [
        {
            "kind": "artifact",
            "ref": "runs/run-123/accepted.json",
            "label": "accepted.json",
            "href": "runs/run-123/accepted.json",
        }
    ]


def test_render_trace_gate_operator_view_lines_is_tui_safe() -> None:
    view = build_trace_gate_operator_view(
        run_id="run-123",
        report=CrossRuntimeTraceFindingReport.model_validate(_REPORT_DATA),
        proposals=[_proposal("681", "accepted", "Accepted on heldout validation.")],
    )

    assert render_trace_gate_operator_view_lines(view) == [
        "Trace gates for run-123 — ready",
        "2 finding(s) across 1 category.",
        "Findings:",
        "- [high/tool_call_failure] Patch tool failed twice evidence=msg #1, msg #3 proposals=01HX0000000000000000000681",
        "- [medium/low_outcome_score] Outcome stayed below threshold evidence=none proposals=none",
        "Recurring failure modes:",
        "- tool_call_failure x2 evidence=msg #1, msg #3",
        "Proposals:",
        "- 01HX0000000000000000000681 accepted target=prompt patches=1 — accepted proposal for trace finding",
        "Gate decisions:",
        "- 01HX0000000000000000000681 accepted heldout — Accepted on heldout validation.",
    ]


def test_trace_gate_operator_view_handles_missing_incomplete_and_no_findings() -> None:
    assert build_trace_gate_operator_view(run_id="run-123", report=None)["state"] == "missing_report"
    assert (
        build_trace_gate_operator_view(run_id="run-123", report=None, analysis_state="incomplete")["state"]
        == "incomplete_analysis"
    )
    no_findings = CrossRuntimeTraceFindingReport.model_validate(
        {**_REPORT_DATA, "findings": [], "failureMotifs": [], "summary": "No notable findings."}
    )
    assert build_trace_gate_operator_view(run_id="run-123", report=no_findings, proposals=[])["state"] == "no_findings"
