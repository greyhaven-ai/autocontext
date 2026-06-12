import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { buildTraceGateReviewApiRoutes } from "../src/server/trace-gate-review-api.js";
import type { TraceFindingReport } from "../src/analytics/trace-findings.js";

const report: TraceFindingReport = {
  reportId: "report-run-123",
  traceId: "trace-run-123",
  sourceHarness: "autocontext",
  createdAt: "2026-06-01T12:00:00.000Z",
  summary: "No notable findings.",
  metadata: {},
  findings: [],
  failureMotifs: [],
};

describe("trace gate review API routes", () => {
  it("returns the operator view for a run from injected report and proposal loaders", () => {
    const api = buildTraceGateReviewApiRoutes({
      runsRoot: "/tmp/runs",
      loadReport: (runId) => (runId === "run-123" ? report : null),
      loadProposals: () => [],
    });

    expect(api.getByRunId("run-123")).toMatchObject({
      status: 200,
      body: {
        run_id: "run-123",
        state: "no_findings",
        report: { report_id: "report-run-123" },
      },
    });
  });

  it("rejects symlinked run directories that resolve outside runs root", () => {
    const root = mkdtempSync(join(tmpdir(), "trace-gate-runs-"));
    const outside = mkdtempSync(join(tmpdir(), "trace-gate-outside-"));
    try {
      writeFileSync(join(outside, "trace-finding-report.json"), JSON.stringify(report));
      symlinkSync(outside, join(root, "link"), "dir");
      const api = buildTraceGateReviewApiRoutes({ runsRoot: root });

      expect(api.getByRunId("link")).toMatchObject({
        status: 422,
        body: { detail: "run_id escapes runs root: 'link'" },
      });
    } finally {
      rmSync(root, { recursive: true, force: true });
      rmSync(outside, { recursive: true, force: true });
    }
  });

  it("rejects invalid proposal files instead of surfacing trusted gate decisions", () => {
    const root = mkdtempSync(join(tmpdir(), "trace-gate-runs-"));
    try {
      const runRoot = join(root, "run-123");
      mkdirSync(join(runRoot, "harness-proposals"), { recursive: true });
      writeFileSync(join(runRoot, "trace-finding-report.json"), JSON.stringify(report));
      writeFileSync(
        join(runRoot, "harness-proposals", "invalid.json"),
        JSON.stringify({
          schemaVersion: "1.0",
          id: "01HX0000000000000000000683",
          status: "accepted",
          findingIds: ["finding-tool-1"],
          targetSurface: "prompt",
          proposedEdit: {
            summary: "accepted proposal for trace finding",
            patches: [
              { filePath: "prompt.txt", operation: "modify", unifiedDiff: "--- a\n+++ b\n" },
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
          decision: {
            status: "accepted",
            reason: "Missing promotion-grade fields.",
            validation: {
              mode: "heldout",
              suiteId: "heldout-suite",
              evidenceRefs: ["accepted.json"],
            },
            decidedAt: "2026-06-01T12:10:00.000Z",
          },
        }),
      );
      const api = buildTraceGateReviewApiRoutes({ runsRoot: root });

      expect(api.getByRunId("run-123")).toMatchObject({
        status: 500,
        body: { detail: expect.stringContaining("Invalid HarnessChangeProposal") },
      });
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("handles missing reports and invalid run ids without throwing raw errors", () => {
    const api = buildTraceGateReviewApiRoutes({
      runsRoot: "/tmp/runs",
      loadReport: () => null,
      loadProposals: () => [],
    });

    expect(api.getByRunId("run-404")).toMatchObject({
      status: 200,
      body: {
        run_id: "run-404",
        state: "missing_report",
        findings: [],
        gate_decisions: [],
      },
    });
    expect(api.getByRunId("../escape")).toMatchObject({
      status: 422,
      body: { detail: "run_id escapes runs root: '../escape'" },
    });
  });
});
