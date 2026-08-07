import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Ajv2020 from "ajv/dist/2020.js";
import type { AnySchema } from "ajv";
import { describe, expect, it } from "vitest";

import {
  buildGoalRunReport,
  parseGoalRunReport,
  type BuildGoalRunReportInput,
  type GoalRunReport,
} from "../src/analytics/goal-run-report.js";
import { readGoalRunReport, writeGoalRunReport } from "../src/knowledge/goal-run-report-store.js";

const fixture = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "goal-run-report-parity-fixture.json"),
    "utf-8",
  ),
) as {
  cases: Array<
    BuildGoalRunReportInput & {
      name: string;
      expected_report: GoalRunReport;
    }
  >;
};
const schema: AnySchema = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "docs", "goal-run-report.json"), "utf-8"),
);

describe("goal run report", () => {
  it("matches the shared Python/TypeScript parity fixture", () => {
    for (const item of fixture.cases) {
      expect(buildGoalRunReport(item)).toEqual(item.expected_report);
    }
  });

  it("parses durable report JSON and rejects schema-invalid data", () => {
    const report = fixture.cases[0]!.expected_report;
    const { max_iterations: _maxIterations, ...missingBudgetField } = report.budget;

    expect(parseGoalRunReport(report)).toEqual(report);
    expect(() => parseGoalRunReport({ ...report, surprise: true })).toThrow(/unexpected field/);
    expect(() => parseGoalRunReport({ ...report, goal_id: "" })).toThrow(/goal_id/);
    expect(() => parseGoalRunReport({ ...report, budget: missingBudgetField })).toThrow(
      /missing field/,
    );
    expect(() =>
      parseGoalRunReport({ ...report, usage: { ...report.usage, iterations: -1 } }),
    ).toThrow(/iterations/);
    expect(() =>
      parseGoalRunReport({
        ...report,
        actions: [{ ...report.actions[0]!, action_kind: "unknown" }],
      }),
    ).toThrow(/action_kind/);
    expect(() => parseGoalRunReport({ ...report, status: "active" })).toThrow(/status/);
  });

  it("covers terminal and continued statuses", () => {
    expect(new Set(fixture.cases.map((item) => item.expected_report.status))).toEqual(
      new Set([
        "continued",
        "verified_complete",
        "blocked",
        "budget_exhausted",
        "verifier_failed",
        "no_progress",
        "canceled",
      ]),
    );
  });

  it("persists goal run reports for resume", () => {
    const root = mkdtempSync(join(tmpdir(), "goal-run-"));
    try {
      const report = parseGoalRunReport(fixture.cases[0]!.expected_report);
      const knowledgeRoot = join(root, "knowledge");

      writeGoalRunReport(knowledgeRoot, report.goal_id, report.goal_run_id, report);

      expect(readGoalRunReport(knowledgeRoot, report.goal_id, report.goal_run_id)).toEqual(report);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("rejects inconsistent decisions and conflicting verifier states", () => {
    const continued = fixture.cases[0]!.expected_report;
    const terminal = fixture.cases[1]!.expected_report;

    for (const payload of [
      { ...continued, status: "verified_complete" },
      { ...continued, decision: { ...continued.decision, next_action_kind: null } },
      { ...terminal, decision: { ...terminal.decision, next_action_kind: "mission" } },
      { ...terminal, decision: { ...terminal.decision, stop_reason: "blocked" } },
      { ...terminal, verifier_state: { ...terminal.verifier_state, verifier_failed: true } },
    ]) {
      expect(() => parseGoalRunReport(payload)).toThrow();
    }

    expect(() =>
      buildGoalRunReport({
        ...fixture.cases[1]!,
        verifier_state: { ...fixture.cases[1]!.verifier_state, verifier_failed: true },
      }),
    ).toThrow(/verified/);
  });

  it("schema rejects inconsistent durable decisions", () => {
    const validate = new Ajv2020({ allErrors: true, strict: true }).compile(schema);
    const continued = fixture.cases[0]!.expected_report;
    const terminal = fixture.cases[1]!.expected_report;

    for (const item of fixture.cases) expect(validate(item.expected_report)).toBe(true);
    expect(validate({ ...continued, status: "verified_complete" })).toBe(false);
    expect(validate({ ...continued, decision: { ...continued.decision, next_action_kind: null } })).toBe(
      false,
    );
    expect(validate({ ...terminal, decision: { ...terminal.decision, next_action_kind: "mission" } })).toBe(
      false,
    );
    expect(validate({ ...terminal, decision: { ...terminal.decision, stop_reason: "blocked" } })).toBe(
      false,
    );
    expect(validate({ ...terminal, verifier_state: { ...terminal.verifier_state, verifier_failed: true } })).toBe(
      false,
    );
  });

  it("rejects path traversal through file helper identifiers", () => {
    const root = mkdtempSync(join(tmpdir(), "goal-run-"));
    try {
      const report = parseGoalRunReport(fixture.cases[0]!.expected_report);
      const knowledgeRoot = join(root, "knowledge");

      expect(() =>
        writeGoalRunReport(knowledgeRoot, "../../../outside", report.goal_run_id, report),
      ).toThrow(/goalId/);
      expect(() =>
        writeGoalRunReport(knowledgeRoot, report.goal_id, "../../../outside", report),
      ).toThrow(/goalRunId/);
      expect(() => writeGoalRunReport(knowledgeRoot, "C:foo", report.goal_run_id, report)).toThrow(
        /goalId/,
      );
      expect(() => writeGoalRunReport(knowledgeRoot, report.goal_id, "C:foo", report)).toThrow(
        /goalRunId/,
      );
      expect(existsSync(join(root, "outside.json"))).toBe(false);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
