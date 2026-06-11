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
