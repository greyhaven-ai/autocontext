/**
 * AC-447: First-class `investigate` command.
 *
 * Tests the investigation engine that takes plain-language problem
 * descriptions, builds investigation specs, gathers evidence,
 * evaluates hypotheses, and returns structured findings with
 * confidence and uncertainty.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  InvestigationEngine,
  type InvestigationRequest,
  type InvestigationResult,
} from "../src/investigation/engine.js";
import type { LLMProvider } from "../src/types/index.js";

// ---------------------------------------------------------------------------
// Mock provider
// ---------------------------------------------------------------------------

function mockProvider(responses?: string[]): LLMProvider {
  let callIndex = 0;
  const defaultSpec = JSON.stringify({
    description: "Investigate system anomaly",
    environment_description: "Production environment",
    initial_state_description: "Anomaly detected",
    evidence_pool_description: "System logs and metrics",
    diagnosis_target: "root cause of anomaly",
    success_criteria: ["identify root cause", "gather supporting evidence"],
    failure_modes: ["inconclusive", "false attribution"],
    max_steps: 8,
    actions: [
      { name: "check_logs", description: "Check system logs", parameters: {}, preconditions: [], effects: ["logs_checked"] },
      { name: "check_metrics", description: "Check performance metrics", parameters: {}, preconditions: [], effects: ["metrics_checked"] },
      { name: "review_changes", description: "Review recent changes", parameters: {}, preconditions: [], effects: ["changes_reviewed"] },
    ],
    evidence_pool: [
      { id: "log_error", content: "Error spike at 14:23", isRedHerring: false, relevance: 0.9 },
      { id: "deploy_change", content: "Config change deployed at 14:20", isRedHerring: false, relevance: 0.8 },
      { id: "unrelated_alert", content: "Disk usage warning on dev server", isRedHerring: true, relevance: 0.1 },
    ],
    correct_diagnosis: "config change caused error spike",
  });
  const defaultHypotheses = JSON.stringify({
    hypotheses: [
      { statement: "Config change caused the error spike", confidence: 0.7 },
      { statement: "Infrastructure degradation", confidence: 0.2 },
      { statement: "Traffic spike overloaded the system", confidence: 0.1 },
    ],
    question: "What caused the error spike?",
  });
  const defaults = [defaultSpec, defaultHypotheses];
  return {
    complete: async () => {
      const text = responses?.[callIndex % (responses?.length ?? 1)] ?? defaults[callIndex % defaults.length];
      callIndex++;
      return { text };
    },
    defaultModel: () => "test-model",
  } as unknown as LLMProvider;
}

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-447-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Investigation engine — core flow
// ---------------------------------------------------------------------------

describe("InvestigationEngine — single investigation", () => {
  it("runs an investigation from plain-language description", async () => {
    const engine = new InvestigationEngine(mockProvider(), tmpDir);

    const result = await engine.run({
      description: "Investigate why our conversion rate dropped after Tuesday's release",
    });

    expect(result.status).toBe("completed");
    expect(result.id).toBeTruthy();
    expect(result.family).toBe("investigation");
    expect(result.question).toBeTruthy();
    expect(result.hypotheses.length).toBeGreaterThan(0);
    expect(result.conclusion).toBeDefined();
    expect(result.unknowns).toBeDefined();
  });

  it("produces hypotheses with confidence scores", async () => {
    const engine = new InvestigationEngine(mockProvider(), tmpDir);

    const result = await engine.run({
      description: "Investigate intermittent CI failures",
    });

    for (const h of result.hypotheses) {
      expect(typeof h.statement).toBe("string");
      expect(typeof h.confidence).toBe("number");
      expect(h.confidence).toBeGreaterThanOrEqual(0);
      expect(h.confidence).toBeLessThanOrEqual(1);
      expect(["supported", "contradicted", "unresolved"]).toContain(h.status);
    }
  });

  it("includes evidence with provenance", async () => {
    const engine = new InvestigationEngine(mockProvider(), tmpDir);

    const result = await engine.run({
      description: "Investigate performance degradation",
    });

    expect(result.evidence.length).toBeGreaterThan(0);
    for (const e of result.evidence) {
      expect(typeof e.id).toBe("string");
      expect(typeof e.summary).toBe("string");
      expect(Array.isArray(e.supports)).toBe(true);
      expect(Array.isArray(e.contradicts)).toBe(true);
    }
  });

  it("produces a conclusion with confidence and limitations", async () => {
    const engine = new InvestigationEngine(mockProvider(), tmpDir);

    const result = await engine.run({
      description: "Investigate error spike",
    });

    expect(typeof result.conclusion.bestExplanation).toBe("string");
    expect(typeof result.conclusion.confidence).toBe("number");
    expect(Array.isArray(result.conclusion.limitations)).toBe(true);
  });

  it("surfaces unknowns and recommended next steps", async () => {
    const engine = new InvestigationEngine(mockProvider(), tmpDir);

    const result = await engine.run({
      description: "Investigate anomaly",
    });

    expect(Array.isArray(result.unknowns)).toBe(true);
    expect(Array.isArray(result.recommendedNextSteps)).toBe(true);
  });

  it("persists durable artifacts", async () => {
    const engine = new InvestigationEngine(mockProvider(), tmpDir);

    const result = await engine.run({
      description: "Investigate something",
    });

    expect(result.artifacts.investigationDir).toBeTruthy();
    expect(existsSync(result.artifacts.investigationDir)).toBe(true);
    expect(existsSync(join(result.artifacts.investigationDir, "spec.json"))).toBe(true);
    expect(existsSync(join(result.artifacts.investigationDir, "report.json"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Investigation options
// ---------------------------------------------------------------------------

describe("InvestigationEngine — options", () => {
  it("respects maxSteps", async () => {
    const engine = new InvestigationEngine(mockProvider(), tmpDir);

    const result = await engine.run({
      description: "Quick investigation",
      maxSteps: 3,
    });

    expect(result.status).toBe("completed");
    expect(result.stepsExecuted).toBeLessThanOrEqual(4);
  });

  it("saves with custom name via saveAs", async () => {
    const engine = new InvestigationEngine(mockProvider(), tmpDir);

    const result = await engine.run({
      description: "Named investigation",
      saveAs: "checkout_rca",
    });

    expect(result.name).toBe("checkout_rca");
    expect(result.artifacts.investigationDir).toContain("checkout_rca");
  });
});

// ---------------------------------------------------------------------------
// InvestigationResult contract
// ---------------------------------------------------------------------------

describe("InvestigationResult contract", () => {
  it("matches the proposed output contract from AC-447", async () => {
    const engine = new InvestigationEngine(mockProvider(), tmpDir);
    const result: InvestigationResult = await engine.run({
      description: "Test result shape",
    });

    // Required fields per AC-447
    expect(result).toHaveProperty("id");
    expect(result).toHaveProperty("name");
    expect(result).toHaveProperty("family");
    expect(result).toHaveProperty("status");
    expect(result).toHaveProperty("description");
    expect(result).toHaveProperty("question");
    expect(result).toHaveProperty("hypotheses");
    expect(result).toHaveProperty("evidence");
    expect(result).toHaveProperty("conclusion");
    expect(result).toHaveProperty("unknowns");
    expect(result).toHaveProperty("recommendedNextSteps");
    expect(result).toHaveProperty("artifacts");

    expect(Array.isArray(result.hypotheses)).toBe(true);
    expect(Array.isArray(result.evidence)).toBe(true);
    expect(Array.isArray(result.unknowns)).toBe(true);
    expect(typeof result.conclusion).toBe("object");
  });
});
