/**
 * AC-448: First-class `analyze` surface.
 *
 * Tests the analysis engine that interprets completed runs, missions,
 * simulations, and investigations — producing structured explanations
 * with attribution, regressions, and uncertainty.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  AnalysisEngine,
  type AnalysisRequest,
  type AnalysisResult,
  type CompareRequest,
} from "../src/analysis/engine.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-448-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// Helper: write a simulation report artifact
function writeSimReport(name: string, data: Record<string, unknown>): string {
  const dir = join(tmpDir, "_simulations", name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "report.json"), JSON.stringify(data, null, 2), "utf-8");
  return dir;
}

// Helper: write an investigation report artifact
function writeInvReport(name: string, data: Record<string, unknown>): string {
  const dir = join(tmpDir, "_investigations", name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "report.json"), JSON.stringify(data, null, 2), "utf-8");
  return dir;
}

// ---------------------------------------------------------------------------
// Single-target analysis
// ---------------------------------------------------------------------------

describe("AnalysisEngine — single target", () => {
  it("analyzes a simulation result", () => {
    writeSimReport("deploy_sim", {
      name: "deploy_sim", family: "simulation", status: "completed",
      summary: { score: 0.85, reasoning: "Good", dimensionScores: { completion: 0.9, recovery: 0.7 } },
      assumptions: ["Bounded to 10 steps"],
      warnings: ["Model-driven result"],
    });

    const engine = new AnalysisEngine(tmpDir);
    const result = engine.analyze({ id: "deploy_sim", type: "simulation" });

    expect(result.target.type).toBe("simulation");
    expect(result.target.id).toBe("deploy_sim");
    expect(result.mode).toBe("single");
    expect(result.summary.headline).toBeTruthy();
    expect(typeof result.summary.confidence).toBe("number");
    expect(result.findings.length).toBeGreaterThan(0);
  });

  it("analyzes an investigation result", () => {
    writeInvReport("checkout_rca", {
      name: "checkout_rca", family: "investigation", status: "completed",
      question: "Why did conversion drop?",
      hypotheses: [
        { statement: "Config change", confidence: 0.74, status: "supported" },
        { statement: "Traffic spike", confidence: 0.2, status: "contradicted" },
      ],
      evidence: [
        { id: "e1", summary: "Error spike at 14:23", supports: ["h0"] },
      ],
      conclusion: { bestExplanation: "Config change", confidence: 0.74, limitations: [] },
    });

    const engine = new AnalysisEngine(tmpDir);
    const result = engine.analyze({ id: "checkout_rca", type: "investigation" });

    expect(result.target.type).toBe("investigation");
    expect(result.findings.some((f) => f.kind === "conclusion")).toBe(true);
  });

  it("returns error for nonexistent artifact", () => {
    const engine = new AnalysisEngine(tmpDir);
    const result = engine.analyze({ id: "nonexistent", type: "simulation" });

    expect(result.summary.headline).toContain("not found");
    expect(result.limitations.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Compare mode
// ---------------------------------------------------------------------------

describe("AnalysisEngine — compare", () => {
  it("compares two simulation results", () => {
    writeSimReport("sim_a", {
      name: "sim_a", family: "simulation", status: "completed",
      summary: { score: 0.6, reasoning: "Mediocre", dimensionScores: { completion: 0.5, recovery: 0.7 } },
    });
    writeSimReport("sim_b", {
      name: "sim_b", family: "simulation", status: "completed",
      summary: { score: 0.9, reasoning: "Great", dimensionScores: { completion: 0.95, recovery: 0.85 } },
    });

    const engine = new AnalysisEngine(tmpDir);
    const result = engine.compare({
      left: { id: "sim_a", type: "simulation" },
      right: { id: "sim_b", type: "simulation" },
    });

    expect(result.mode).toBe("compare");
    expect(result.summary.headline).toBeTruthy();
    expect(result.findings.some((f) => f.kind === "improvement" || f.kind === "regression" || f.kind === "driver")).toBe(true);
    expect(result.attribution).toBeDefined();
    expect(result.attribution!.topFactors.length).toBeGreaterThan(0);
  });

  it("identifies regressions in compare mode", () => {
    writeSimReport("before", {
      name: "before", family: "simulation", status: "completed",
      summary: { score: 0.9, dimensionScores: { completion: 0.95, recovery: 0.85 } },
    });
    writeSimReport("after", {
      name: "after", family: "simulation", status: "completed",
      summary: { score: 0.5, dimensionScores: { completion: 0.4, recovery: 0.6 } },
    });

    const engine = new AnalysisEngine(tmpDir);
    const result = engine.compare({
      left: { id: "before", type: "simulation" },
      right: { id: "after", type: "simulation" },
    });

    expect(result.regressions.length).toBeGreaterThan(0);
  });

  it("fails honestly for incompatible types", () => {
    writeSimReport("sim", {
      name: "sim", family: "simulation", status: "completed",
      summary: { score: 0.8 },
    });
    writeInvReport("inv", {
      name: "inv", family: "investigation", status: "completed",
      conclusion: { bestExplanation: "X", confidence: 0.7 },
    });

    const engine = new AnalysisEngine(tmpDir);
    const result = engine.compare({
      left: { id: "sim", type: "simulation" },
      right: { id: "inv", type: "investigation" },
    });

    expect(result.limitations.some((l) => l.toLowerCase().includes("different") || l.toLowerCase().includes("type"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// AnalysisResult contract
// ---------------------------------------------------------------------------

describe("AnalysisResult contract", () => {
  it("has all required fields per AC-448", () => {
    writeSimReport("shape_test", {
      name: "shape_test", family: "simulation", status: "completed",
      summary: { score: 0.75, dimensionScores: {} },
    });

    const engine = new AnalysisEngine(tmpDir);
    const result: AnalysisResult = engine.analyze({ id: "shape_test", type: "simulation" });

    expect(result).toHaveProperty("id");
    expect(result).toHaveProperty("target");
    expect(result).toHaveProperty("mode");
    expect(result).toHaveProperty("summary");
    expect(result).toHaveProperty("findings");
    expect(result).toHaveProperty("regressions");
    expect(result).toHaveProperty("limitations");
    expect(result).toHaveProperty("artifacts");

    expect(typeof result.summary.headline).toBe("string");
    expect(typeof result.summary.confidence).toBe("number");
    expect(Array.isArray(result.findings)).toBe(true);
    expect(Array.isArray(result.regressions)).toBe(true);
    expect(Array.isArray(result.limitations)).toBe(true);
  });
});
