import { describe, expect, it } from "vitest";

import {
  buildTraceGateOperatorView,
  renderTraceGateOperatorViewLines,
} from "../src/analytics/trace-gate-operator-view.js";
import type { TraceFindingReport } from "../src/analytics/trace-findings.js";
import type { HarnessChangeProposal } from "../src/control-plane/contract/types.js";

const report: TraceFindingReport = {
  reportId: "report-run-123",
  traceId: "trace-run-123",
  sourceHarness: "autocontext",
  createdAt: "2026-06-01T12:00:00.000Z",
  summary: "2 finding(s) across 1 category.",
  metadata: {},
  findings: [
    {
      findingId: "finding-tool-1",
      category: "tool_call_failure",
      severity: "high",
      title: "Patch tool failed twice",
      description: "patch hunk did not apply",
      evidenceMessageIndexes: [1, 3],
    },
    {
      findingId: "finding-score-1",
      category: "low_outcome_score",
      severity: "medium",
      title: "Outcome stayed below threshold",
      description: "score=0.32",
      evidenceMessageIndexes: [],
    },
  ],
  failureMotifs: [
    {
      motifId: "motif-tool",
      category: "tool_call_failure",
      occurrenceCount: 2,
      evidenceMessageIndexes: [1, 3],
      description: "patch tool failures repeated",
    },
  ],
};

function proposal(
  suffix: string,
  status: "accepted" | "rejected" | "inconclusive" | "proposed",
  reason = "",
): HarnessChangeProposal {
  return {
    schemaVersion: "1.0" as HarnessChangeProposal["schemaVersion"],
    id: `01HX0000000000000000000${suffix}` as HarnessChangeProposal["id"],
    status,
    findingIds: ["finding-tool-1"],
    targetSurface: suffix === "683" ? "verifier-rubric" : "prompt",
    proposedEdit: {
      summary: `${status} proposal for trace finding`,
      patches: [
        {
          filePath: "agents/grid_ctf/prompts/competitor.txt",
          operation: "modify",
          unifiedDiff: "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
        },
      ],
    },
    expectedImpact: { qualityDelta: 0.08, riskReduction: "fewer repeat tool failures" },
    rollbackCriteria: ["heldout score regresses"],
    provenance: {
      authorType: "autocontext-run",
      authorId: "run-123",
      parentArtifactIds: [],
      createdAt: "2026-06-01T12:05:00.000Z",
    },
    ...(status === "proposed"
      ? {}
      : {
          decision: {
            status,
            reason,
            validation: {
              mode: status === "inconclusive" ? "dev" : "heldout",
              suiteId: "heldout-suite" as never,
              evidenceRefs: status === "inconclusive" ? [] : [`runs/run-123/${status}.json`],
            },
            promotionDecision: {
              schemaVersion: "1.0" as HarnessChangeProposal["schemaVersion"],
              pass: status === "accepted",
              recommendedTargetState: status === "accepted" ? "canary" : "disabled",
              deltas: {
                quality: { baseline: 0.6, candidate: 0.72, delta: 0.12, passed: true },
                cost: {
                  baseline: { tokensIn: 100, tokensOut: 50 },
                  candidate: { tokensIn: 110, tokensOut: 55 },
                  delta: { tokensIn: 10, tokensOut: 5 },
                  passed: true,
                },
                latency: {
                  baseline: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
                  candidate: { p50Ms: 11, p95Ms: 21, p99Ms: 31 },
                  delta: { p50Ms: 1, p95Ms: 1, p99Ms: 1 },
                  passed: true,
                },
                safety: { regressions: [], passed: true },
              },
              confidence: 0.9,
              thresholds: {
                qualityMinDelta: 0.05,
                costMaxRelativeIncrease: 0.2,
                latencyMaxRelativeIncrease: 0.2,
                strongConfidenceMin: 0.9,
                moderateConfidenceMin: 0.7,
                strongQualityMultiplier: 2,
              },
              reasoning: reason,
              evaluatedAt: "2026-06-01T12:10:00.000Z",
            },
            candidateArtifactId: "01HX0000000000000000000001" as never,
            candidateEvalRunId: `candidate-${status}`,
            baselineArtifactId: "01HX0000000000000000000002" as never,
            baselineEvalRunId: `baseline-${status}`,
            decidedAt: "2026-06-01T12:10:00.000Z",
          },
        }),
  };
}

describe("trace gate operator view", () => {
  it("surfaces findings, recurring failure modes, proposals, gate decisions, and evidence links", () => {
    const view = buildTraceGateOperatorView({
      run_id: "run-123",
      report,
      proposals: [
        proposal("681", "accepted", "Accepted on heldout validation."),
        proposal("682", "rejected", "Rejected on heldout validation."),
        proposal("683", "inconclusive", "Dev-only validation is not enough."),
      ],
    });

    expect(view).toMatchObject({
      schema_version: "1",
      run_id: "run-123",
      state: "ready",
      report: {
        report_id: "report-run-123",
        trace_id: "trace-run-123",
        finding_count: 2,
        failure_mode_count: 1,
      },
    });
    expect(view.findings[0]).toMatchObject({
      finding_id: "finding-tool-1",
      evidence_count: 2,
      linked_proposal_ids: [
        "01HX0000000000000000000681",
        "01HX0000000000000000000682",
        "01HX0000000000000000000683",
      ],
      evidence_refs: [
        { kind: "trace_message", ref: "msg:1", label: "msg #1", href: "#msg-1" },
        { kind: "trace_message", ref: "msg:3", label: "msg #3", href: "#msg-3" },
      ],
    });
    expect(view.failure_modes).toEqual([
      {
        motif_id: "motif-tool",
        category: "tool_call_failure",
        occurrence_count: 2,
        description: "patch tool failures repeated",
        representative_evidence_refs: [
          { kind: "trace_message", ref: "msg:1", label: "msg #1", href: "#msg-1" },
          { kind: "trace_message", ref: "msg:3", label: "msg #3", href: "#msg-3" },
        ],
      },
    ]);
    expect(view.proposals.map((item) => [item.status, item.target_surface])).toEqual([
      ["accepted", "prompt"],
      ["rejected", "prompt"],
      ["inconclusive", "verifier-rubric"],
    ]);
    expect(view.gate_decisions.map((decision) => decision.status)).toEqual([
      "accepted",
      "rejected",
      "inconclusive",
    ]);
    expect(view.gate_decisions[0]?.evidence_refs).toEqual([
      {
        kind: "artifact",
        ref: "runs/run-123/accepted.json",
        label: "accepted.json",
        href: "runs/run-123/accepted.json",
      },
    ]);
  });

  it("renders TUI-safe lines without raw JSON for operator inspection", () => {
    const view = buildTraceGateOperatorView({
      run_id: "run-123",
      report,
      proposals: [proposal("681", "accepted", "Accepted on heldout validation.")],
    });

    expect(renderTraceGateOperatorViewLines(view)).toEqual([
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
    ]);
  });

  it("handles missing, incomplete, and no-finding states gracefully", () => {
    expect(buildTraceGateOperatorView({ run_id: "run-123", report: null }).state).toBe(
      "missing_report",
    );
    expect(
      buildTraceGateOperatorView({ run_id: "run-123", report: null, analysis_state: "incomplete" })
        .state,
    ).toBe("incomplete_analysis");
    expect(
      buildTraceGateOperatorView({
        run_id: "run-123",
        report: { ...report, findings: [], failureMotifs: [], summary: "No notable findings." },
        proposals: [],
      }).state,
    ).toBe("no_findings");
  });
});
