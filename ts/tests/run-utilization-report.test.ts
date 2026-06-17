import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  buildRunUtilizationReport,
  parseRunUtilizationReport,
  type RunUtilizationEventInput,
  type RunUtilizationReport,
  type RunUtilizationRoleUsageInput,
} from "../src/analytics/run-utilization-report.js";

const reportSchema = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "run-utilization-report.json"),
    "utf-8",
  ),
) as { required?: string[] };

const fixture = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "run-utilization-report-parity-fixture.json"),
    "utf-8",
  ),
) as {
  cases: Array<{
    name: string;
    run_id: string;
    generated_at: string;
    events: RunUtilizationEventInput[];
    role_usage: RunUtilizationRoleUsageInput[];
    expected_report: RunUtilizationReport;
  }>;
};

function withoutKey(item: object, key: string): Record<string, unknown> {
  const copy = { ...(item as Record<string, unknown>) };
  delete copy[key];
  return copy;
}

describe("run utilization report", () => {
  it("matches the shared Python/TypeScript parity fixture", () => {
    for (const item of fixture.cases) {
      expect(
        buildRunUtilizationReport({
          runId: item.run_id,
          generatedAt: item.generated_at,
          events: item.events,
          roleUsage: item.role_usage,
        }),
      ).toEqual(item.expected_report);
    }
  });

  it("parses durable report JSON and rejects schema-invalid data", () => {
    const report = fixture.cases[1]!.expected_report;

    expect(parseRunUtilizationReport(report)).toEqual(report);
    expect(reportSchema.required).toContain("token_utilization");
    expect(reportSchema.required).toContain("evaluation_utilization");
    expect(() => parseRunUtilizationReport({ ...report, surprise: true })).toThrow(
      /unexpected field/,
    );
    expect(() => parseRunUtilizationReport({ ...report, run_id: "" })).toThrow(/run_id/);
    expect(() =>
      parseRunUtilizationReport({
        ...report,
        window: withoutKey(report.window, "duration_seconds"),
      }),
    ).toThrow(/missing field/);
    expect(() =>
      parseRunUtilizationReport({
        ...report,
        token_utilization: withoutKey(report.token_utilization, "model_wait_seconds"),
      }),
    ).toThrow(/missing field/);
    expect(() =>
      parseRunUtilizationReport({
        ...report,
        token_utilization: { ...report.token_utilization, input_tokens: -1 },
      }),
    ).toThrow(/input_tokens/);
    expect(() =>
      parseRunUtilizationReport({
        ...report,
        evaluation_utilization: { ...report.evaluation_utilization, eval_count: -1 },
      }),
    ).toThrow(/eval_count/);
  });

  it("does not emit negative token counts from malformed usage telemetry", () => {
    const report = buildRunUtilizationReport({
      runId: "negative-token-run",
      generatedAt: "2026-06-16T12:00:00Z",
      events: [
        {
          event_type: "evaluation_finished",
          timestamp: "2026-06-16T12:00:01Z",
          branch_id: "branch-a",
          duration_seconds: 1,
          verifier_passed: true,
        },
      ],
      roleUsage: [
        {
          timestamp: "2026-06-16T12:00:00Z",
          branch_id: "branch-a",
          input_tokens: -10,
          output_tokens: -5,
        },
      ],
    });

    expect(report.token_utilization.input_tokens).toBe(0);
    expect(report.token_utilization.output_tokens).toBe(0);
    expect(report.token_utilization.total_tokens).toBe(0);
    expect(report.token_utilization.tokens_to_success).toBe(0);
  });
});
