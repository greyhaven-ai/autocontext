import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  buildRunProgressReport,
  parseRunProgressReport,
  type RunProgressEvent,
  type RunProgressReport,
} from "../src/analytics/progress-report.js";
import { renderRunStatus } from "../src/cli/run-inspection-command-workflow.js";

const reportSchema = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "docs", "run-progress-report.json"), "utf-8"),
) as Record<string, unknown>;

const fixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "docs", "run-progress-report-parity-fixture.json"), "utf-8"),
) as {
  run_id: string;
  threshold: number;
  generated_at: string;
  pass_at_k_values: number[];
  events: RunProgressEvent[];
  expected_report: RunProgressReport;
};

const run = {
  run_id: "run-progress-1",
  scenario: "grid_ctf",
  target_generations: 3,
  executor_mode: "local",
  status: "completed",
  agent_provider: "deterministic",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:02:00Z",
};

describe("run progress report", () => {
  it("matches the shared Python/TypeScript parity fixture", () => {
    const report = buildRunProgressReport({
      runId: fixture.run_id,
      events: fixture.events,
      threshold: fixture.threshold,
      passAtKValues: fixture.pass_at_k_values,
      generatedAt: fixture.generated_at,
    });

    expect(report).toEqual(fixture.expected_report);
  });

  it("parses and documents the durable report JSON shape", () => {
    expect(parseRunProgressReport(fixture.expected_report)).toEqual(fixture.expected_report);
    expect(reportSchema.required).toContain("progress_points");
    expect(reportSchema.required).toContain("pass_at_k");
  });

  it("rejects schema-invalid extra fields", () => {
    expect(() => parseRunProgressReport({ ...fixture.expected_report, surprise: true })).toThrow(
      /unexpected field/,
    );
  });

  it("lets run inspection reference progress curves and pass@k", () => {
    const rendered = renderRunStatus(run, [], false, null, fixture.expected_report);
    const payload = JSON.parse(renderRunStatus(run, [], true, null, fixture.expected_report));

    expect(rendered).toContain("Progress best score: 0.830");
    expect(rendered).toContain("pass@4: pass");
    expect(payload.progress_report.best_score).toBe(0.83);
    expect(payload.progress_report.pass_at_k.at(-1)?.passed).toBe(true);
  });
});
