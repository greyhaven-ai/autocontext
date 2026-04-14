import { afterEach, describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  normalizeConfidence,
  normalizeDecisionMetric,
  normalizePreviewThreshold,
} from "../src/analytics/number-utils.js";
import { AnalysisEngine } from "../src/analysis/engine.js";
import { IntentValidator } from "../src/scenarios/intent-validator.js";

describe("numeric normalization policy", () => {
  it("normalizes logic-sensitive deltas separately from preview thresholds", () => {
    expect(normalizeDecisionMetric(0.123456789)).toBe(0.123457);
    expect(normalizeDecisionMetric(-0.0049999997)).toBe(-0.005);
    expect(normalizePreviewThreshold(0.3333333333)).toBe(0.333);
  });

  it("clamps and rounds confidence-like values to the unit interval", () => {
    expect(normalizeConfidence(0.87654321)).toBe(0.8765);
    expect(normalizeConfidence(4)).toBe(1);
    expect(normalizeConfidence(-0.2)).toBe(0);
  });
});

describe("IntentValidator confidence normalization", () => {
  it("returns a normalized confidence instead of a repeating decimal", () => {
    const validator = new IntentValidator(0.4);
    const result = validator.validate(
      "api latency retries",
      {
        name: "latency_review",
        taskPrompt: "review service handbook",
        rubric: "Score reliability",
        description: "Assess latency guidance",
      },
    );

    expect(result.valid).toBe(false);
    expect(result.confidence).toBe(0.3333);
    expect(result.issues.join(" ")).toContain("0.33");
  });
});

describe("AnalysisEngine confidence normalization", () => {
  let dir: string;

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("clamps and rounds investigation confidence summaries", () => {
    dir = mkdtempSync(join(tmpdir(), "ac-numeric-policy-"));
    const investigationDir = join(dir, "_investigations", "checkout_rca");
    mkdirSync(investigationDir, { recursive: true });
    writeFileSync(
      join(investigationDir, "report.json"),
      JSON.stringify({
        name: "checkout_rca",
        family: "investigation",
        status: "completed",
        conclusion: {
          bestExplanation: "Config drift",
          confidence: 1.234567,
          limitations: [],
        },
      }),
      "utf-8",
    );

    const engine = new AnalysisEngine({
      knowledgeRoot: dir,
      runsRoot: join(dir, "runs"),
      dbPath: join(dir, "autocontext.sqlite3"),
    });

    const result = engine.analyze({ id: "checkout_rca", type: "investigation" });
    expect(result.summary.confidence).toBe(1);
  });
});
