import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, describe, expect, it } from "vitest";

import {
  buildCompletedInvestigationResult,
  persistInvestigationReport,
} from "../src/investigation/investigation-result-workflow.js";

describe("investigation result workflow", () => {
  const dirs: string[] = [];

  afterEach(() => {
    for (const dir of dirs.splice(0)) {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("builds completed investigation results and persists the report payload", () => {
    const dir = mkdtempSync(join(tmpdir(), "ac-investigation-result-"));
    dirs.push(dir);
    const reportPath = join(dir, "report.json");

    const result = buildCompletedInvestigationResult({
      id: "inv-1",
      name: "checkout_rca",
      description: "Investigate checkout regression",
      question: undefined,
      hypotheses: [
        {
          id: "h0",
          statement: "Config change caused the regression",
          status: "supported",
          confidence: 0.8,
        },
      ],
      evidence: [
        {
          id: "e0",
          kind: "observation",
          source: "scenario execution",
          summary: "Config deployed before regression",
          supports: ["h0"],
          contradicts: [],
          isRedHerring: false,
        },
      ],
      conclusion: {
        bestExplanation: "Config change caused the regression",
        confidence: 0.8,
        limitations: ["Investigation based on generated scenario — not live system data"],
      },
      unknowns: ["Need more production telemetry"],
      recommendedNextSteps: ["Verify leading hypothesis: \"Config change caused the regression\""],
      stepsExecuted: 3,
      investigationDir: dir,
      reportPath,
    });

    expect(result).toMatchObject({
      family: "investigation",
      status: "completed",
      question: "What caused: Investigate checkout regression",
      artifacts: {
        investigationDir: dir,
        reportPath,
      },
    });

    persistInvestigationReport(reportPath, result);
    expect(JSON.parse(readFileSync(reportPath, "utf-8"))).toEqual(result);
  });
});
