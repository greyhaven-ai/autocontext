/**
 * AC-457: Align distilled-model training prompts with runtime prompt surface.
 *
 * Tests the prompt contract that ensures training evaluation and runtime
 * invocation speak the same language.
 */

import { describe, it, expect } from "vitest";
import {
  PromptContract,
  RuntimePromptAdapter,
  TrainingPromptAdapter,
  validatePromptAlignment,
  type AlignmentReport,
  buildPromptBundle,
} from "../src/index.js";

// ---------------------------------------------------------------------------
// PromptContract
// ---------------------------------------------------------------------------

describe("PromptContract", () => {
  it("is exported through the package entrypoint", () => {
    expect(typeof PromptContract).toBe("function");
    expect(typeof RuntimePromptAdapter).toBe("function");
    expect(typeof TrainingPromptAdapter).toBe("function");
    expect(typeof validatePromptAlignment).toBe("function");
  });

  it("defines the canonical prompt shape for local models", () => {
    const contract = new PromptContract();
    const shape = contract.shape();

    expect(shape.systemFields).toContain("scenarioRules");
    expect(shape.systemFields).toContain("evaluationCriteria");
    expect(shape.userFields).toContain("task");
    expect(shape.responseFormat).toBeTruthy();
  });

  it("validates a well-formed prompt against the contract", () => {
    const contract = new PromptContract();
    const result = contract.validate({
      system: "## Scenario Rules\nPlay the game\n\n## Evaluation Criteria\nMaximize score",
      user: "Produce a strategy",
    });

    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
  });

  it("rejects a prompt missing required sections", () => {
    const contract = new PromptContract();
    const result = contract.validate({
      system: "Just do something",
      user: "Go",
    });

    expect(result.valid).toBe(false);
    expect(result.errors.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// RuntimePromptAdapter
// ---------------------------------------------------------------------------

describe("RuntimePromptAdapter", () => {
  it("converts a runtime prompt bundle to contract-compatible shape", () => {
    const adapter = new RuntimePromptAdapter();
    const result = adapter.fromBundle({
      competitor: "## Scenario Rules\nGame rules\n\n## Strategy Interface\nJSON\n\n## Evaluation Criteria\nScore\n\n## Your Task\nProduce strategy",
    });

    expect(result.system).toContain("Scenario Rules");
    expect(result.system).toContain("Evaluation Criteria");
    expect(result.user).toContain("Produce strategy");
  });

  it("extracts system and user parts from combined prompt", () => {
    const adapter = new RuntimePromptAdapter();
    const result = adapter.fromBundle({
      competitor: "System context here\n\n## Your Task\nDo the thing",
    });

    expect(result.system).toBeTruthy();
    expect(result.user).toContain("Do the thing");
  });
});

// ---------------------------------------------------------------------------
// TrainingPromptAdapter
// ---------------------------------------------------------------------------

describe("TrainingPromptAdapter", () => {
  it("converts training data to contract-compatible prompt", () => {
    const adapter = new TrainingPromptAdapter();
    const result = adapter.fromTrainingRecord({
      scenario: "grid_ctf",
      strategy: '{"move": "north"}',
      score: 0.85,
      context: {
        scenarioRules: "Capture the flag",
        strategyInterface: "JSON with move field",
        evaluationCriteria: "Maximize captures",
      },
    });

    expect(result.system).toContain("Capture the flag");
    expect(result.system).toContain("Evaluation");
    expect(result.user).toBe("Produce a JSON strategy that maximizes the evaluation criteria.");
    expect(result.expectedOutput).toBe('{"move": "north"}');
  });

  it("matches the real runtime bundle contract for a game scenario", () => {
    const training = new TrainingPromptAdapter().fromTrainingRecord({
      scenario: "grid_ctf",
      strategy: '{"aggression": 0.7}',
      score: 0.85,
      context: {
        scenarioRules: "Capture the flag",
        strategyInterface: "Return JSON with aggression/defense/path_bias",
        evaluationCriteria: "Maximize captures",
        playbook: "Pressure the center lane.",
        hints: "Watch the defender count.",
        trajectory: [
          { generation_index: 1, best_score: 0.65, gate_decision: "advance" },
          { generation_index: 2, best_score: 0.78, gate_decision: "advance" },
        ],
      },
    });
    const runtime = new RuntimePromptAdapter().fromBundle(buildPromptBundle({
      scenarioRules: "Capture the flag",
      strategyInterface: "Return JSON with aggression/defense/path_bias",
      evaluationCriteria: "Maximize captures",
      playbook: "Pressure the center lane.",
      trajectory: "Generation 1: score=0.6500, gate=advance\nGeneration 2: score=0.7800, gate=advance",
      lessons: "",
      tools: "",
      hints: "Watch the defender count.",
      analysis: "",
    }));

    const report = validatePromptAlignment({
      trainingPrompt: training,
      runtimePrompt: runtime,
    });

    expect(training.user).toBe(runtime.user);
    expect(training.system).toContain("Generation 1: score=0.6500, gate=advance");
    expect(report.aligned).toBe(true);
    expect(report.mismatches).toHaveLength(0);
  });

  it("generates a training example in contract format", () => {
    const adapter = new TrainingPromptAdapter();
    const example = adapter.toTrainingExample({
      scenario: "code_review",
      strategy: "Review the auth module for SQL injection",
      score: 0.9,
      context: {
        scenarioRules: "Review code for security vulnerabilities",
        evaluationCriteria: "Find all vulnerabilities",
      },
    });

    expect(example.conversations).toBeDefined();
    expect(example.conversations.length).toBeGreaterThanOrEqual(2);
    expect(example.conversations[0].from).toBe("system");
    expect(example.conversations[1].from).toBe("human");
    // Should have a gpt response with the strategy
    expect(example.conversations.some((c) => c.from === "gpt")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Alignment validation
// ---------------------------------------------------------------------------

describe("validatePromptAlignment", () => {
  it("passes when training and runtime use same contract", () => {
    const report = validatePromptAlignment({
      trainingPrompt: {
        system: "## Scenario Rules\nGame\n\n## Evaluation Criteria\nScore",
        user: "Produce strategy",
      },
      runtimePrompt: {
        system: "## Scenario Rules\nGame\n\n## Evaluation Criteria\nScore",
        user: "Produce strategy",
      },
    });

    expect(report.aligned).toBe(true);
    expect(report.mismatches).toHaveLength(0);
  });

  it("detects when runtime has sections training doesn't", () => {
    const report = validatePromptAlignment({
      trainingPrompt: {
        system: "Just the rules",
        user: "Go",
      },
      runtimePrompt: {
        system: "## Scenario Rules\nRules\n\n## Evaluation Criteria\nScore\n\n## Playbook\nTips",
        user: "Produce strategy",
      },
    });

    expect(report.aligned).toBe(false);
    expect(report.mismatches.length).toBeGreaterThan(0);
  });

  it("reports which sections are misaligned", () => {
    const report = validatePromptAlignment({
      trainingPrompt: {
        system: "## Scenario Rules\nA",
        user: "Do",
      },
      runtimePrompt: {
        system: "## Scenario Rules\nA\n\n## Playbook\nB\n\n## Evaluation Criteria\nC",
        user: "Do it",
      },
    });

    expect(report.mismatches.some((m) => m.includes("Playbook") || m.includes("Evaluation"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// AlignmentReport shape
// ---------------------------------------------------------------------------

describe("AlignmentReport shape", () => {
  it("has all required fields", () => {
    const report: AlignmentReport = validatePromptAlignment({
      trainingPrompt: { system: "s", user: "u" },
      runtimePrompt: { system: "s", user: "u" },
    });

    expect(report).toHaveProperty("aligned");
    expect(report).toHaveProperty("mismatches");
    expect(report).toHaveProperty("trainingSections");
    expect(report).toHaveProperty("runtimeSections");
  });
});
