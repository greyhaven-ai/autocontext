/**
 * AC-442: Deep execution validation for all codegen families.
 *
 * Tests verify that generated code is actually executed and validated
 * before registration — not just checked for method signatures.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, existsSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  validateGeneratedScenario,
  type ExecutionValidationResult,
} from "../src/scenarios/codegen/execution-validator.js";

// ---------------------------------------------------------------------------
// Valid generated code passes execution validation
// ---------------------------------------------------------------------------

describe("execution validation — valid scenarios", () => {
  it("validates a working simulation scenario", async () => {
    const source = `
const scenario = {
  name: "test_sim",
  describeScenario() { return "Test simulation"; },
  describeEnvironment() { return { name: "test", availableActions: [{name: "act1", description: "d", parameters: {}, preconditions: [], effects: []}] }; },
  initialState(seed) { return { seed: seed || 0, step: 0, completedActions: [], terminal: false }; },
  getAvailableActions(state) { return [{name: "act1"}]; },
  executeAction(state, action) {
    return { result: { success: true, output: "done" }, state: { ...state, step: state.step + 1, completedActions: ["act1"] } };
  },
  isTerminal(state) { return state.completedActions?.length >= 1; },
  getResult(state) { return { score: 1.0, reasoning: "ok", dimensionScores: {} }; },
  getRubric() { return "test"; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "simulation",
      "test_sim",
    );
    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
    expect(result.executedMethods).toContain("initialState");
    expect(result.executedMethods).toContain("executeAction");
  });

  it("validates a working agent_task scenario", async () => {
    const source = `
const scenario = {
  name: "test_task",
  getTaskPrompt() { return "Do something"; },
  getRubric() { return "Evaluate quality"; },
  describeTask() { return "A test task"; },
  initialState() { return { round: 0 }; },
  async evaluateOutput(output) { return { score: 0.8, reasoning: "good", dimensionScores: {} }; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "agent_task",
      "test_task",
    );
    expect(result.valid).toBe(true);
    expect(result.executedMethods).toContain("initialState");
    expect(result.executedMethods).toContain("getTaskPrompt");
  });

  it("validates a working operator_loop scenario", async () => {
    const source = `
const ACTIONS = [{ name: "monitor", description: "Monitor system", parameters: {}, preconditions: [], effects: [] }];
const scenario = {
  name: "test_op",
  describeScenario() { return "Test"; },
  describeEnvironment() { return { name: "test", availableActions: ACTIONS }; },
  initialState(seed) { return { seed: seed || 0, step: 0, completedActions: [], escalationLog: [], clarificationLog: [], autonomousActions: 0, situationsRequiringEscalation: [] }; },
  getAvailableActions(state) { return ACTIONS.filter(a => !(state.completedActions || []).includes(a.name)); },
  executeAction(state, action) { return { result: { success: true, output: "" }, state: { ...state, completedActions: [...(state.completedActions || []), action.name] } }; },
  isTerminal() { return true; },
  getResult(state) { return { score: 1, reasoning: "ok", dimensionScores: {} }; },
  getEscalationLog(state) { return state.escalationLog || []; },
  getClarificationLog(state) { return state.clarificationLog || []; },
  escalate(state, event) { return { ...state, escalationLog: [...(state.escalationLog || []), event] }; },
  requestClarification(state, req) { return { ...state, clarificationLog: [...(state.clarificationLog || []), req] }; },
  evaluateJudgment(state) { return { score: 1, reasoning: "ok", dimensionScores: {}, totalActions: 0, escalations: 0, necessaryEscalations: 0, unnecessaryEscalations: 0, missedEscalations: 0, clarificationsRequested: 0 }; },
  getRubric() { return "test"; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "operator_loop",
      "test_op",
    );
    expect(result.valid).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Broken generated code caught by execution validation
// ---------------------------------------------------------------------------

describe("execution validation — broken scenarios", () => {
  it("catches scenario that crashes on initialState", async () => {
    const source = `
const scenario = {
  name: "broken",
  describeScenario() { return "test"; },
  describeEnvironment() { return {}; },
  initialState() { throw new Error("initialization crashed"); },
  getAvailableActions() { return []; },
  executeAction() { return {}; },
  isTerminal() { return true; },
  getResult() { return { score: 0 }; },
  getRubric() { return "test"; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "simulation",
      "broken",
    );
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes("initialState"))).toBe(true);
  });

  it("catches scenario that returns wrong shape from initialState", async () => {
    const source = `
const scenario = {
  name: "bad_state",
  describeScenario() { return "test"; },
  describeEnvironment() { return {}; },
  initialState() { return "not an object"; },
  getAvailableActions() { return []; },
  executeAction() { return {}; },
  isTerminal() { return true; },
  getResult() { return { score: 0 }; },
  getRubric() { return "test"; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "simulation",
      "bad_state",
    );
    expect(result.valid).toBe(false);
    expect(
      result.errors.some(
        (e) => e.includes("initialState") && e.includes("object"),
      ),
    ).toBe(true);
  });

  it("catches scenario with syntax error", async () => {
    const source = `
const scenario = {
  name: "syntax_error",
  describeScenario() { return "test"; },
  initialState() { return {}
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "simulation",
      "syntax_error",
    );
    expect(result.valid).toBe(false);
    expect(result.errors.length).toBeGreaterThan(0);
  });

  it("catches scenario missing required methods", async () => {
    const source = `
const scenario = {
  name: "incomplete",
  describeScenario() { return "test"; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "simulation",
      "incomplete",
    );
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes("missing"))).toBe(true);
  });

  it("catches getResult returning non-numeric score", async () => {
    const source = `
const scenario = {
  name: "bad_score",
  describeScenario() { return "test"; },
  describeEnvironment() { return { availableActions: [{name: "a"}] }; },
  initialState() { return { step: 0, completedActions: [] }; },
  getAvailableActions() { return [{name: "a"}]; },
  executeAction(state) { return { result: { success: true }, state: { ...state, completedActions: ["a"] } }; },
  isTerminal() { return true; },
  getResult() { return { score: "not a number", reasoning: "bad" }; },
  getRubric() { return "test"; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "simulation",
      "bad_score",
    );
    expect(result.valid).toBe(false);
    expect(
      result.errors.some((e) => e.includes("score") && e.includes("number")),
    ).toBe(true);
  });

  it("catches simulation scenarios that only fail when describeEnvironment is executed", async () => {
    const source = `
const scenario = {
  name: "bad_environment",
  describeScenario() { return "test"; },
  describeEnvironment() { throw new Error("environment crashed"); },
  initialState() { return { step: 0, completedActions: [] }; },
  getAvailableActions() { return []; },
  executeAction(state) { return { result: { success: true }, state }; },
  isTerminal() { return true; },
  getResult() { return { score: 1, reasoning: "ok", dimensionScores: {} }; },
  getRubric() { return "test"; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "simulation",
      "bad_environment",
    );
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes("describeEnvironment"))).toBe(
      true,
    );
  });

  it("catches artifact-editing scenarios that only fail when edit methods are executed", async () => {
    const source = `
const scenario = {
  name: "bad_artifact",
  describeTask() { return "Edit the artifact"; },
  getRubric() { return "test"; },
  initialState() { return { round: 0 }; },
  initialArtifacts() { return [{ name: "README.md", content: "hello", format: "text" }]; },
  getEditPrompt() { throw new Error("prompt crashed"); },
  validateArtifact() { return { valid: true, errors: [] }; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "artifact_editing",
      "bad_artifact",
    );
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes("getEditPrompt"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Result shape
// ---------------------------------------------------------------------------

describe("ExecutionValidationResult shape", () => {
  it("includes executedMethods, errors, and timing", async () => {
    const source = `
const scenario = {
  name: "shape_test",
  describeScenario() { return "test"; },
  describeEnvironment() { return { availableActions: [] }; },
  initialState() { return { step: 0 }; },
  getAvailableActions() { return []; },
  executeAction(state) { return { result: { success: true }, state }; },
  isTerminal() { return true; },
  getResult() { return { score: 0.5, reasoning: "ok", dimensionScores: {} }; },
  getRubric() { return "test"; },
};
module.exports = { scenario };
`;
    const result: ExecutionValidationResult = await validateGeneratedScenario(
      source,
      "simulation",
      "shape_test",
    );
    expect(result).toHaveProperty("valid");
    expect(result).toHaveProperty("errors");
    expect(result).toHaveProperty("executedMethods");
    expect(result).toHaveProperty("durationMs");
    expect(typeof result.durationMs).toBe("number");
    expect(Array.isArray(result.executedMethods)).toBe(true);
  });
});

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-execution-validation-"));
}

describe("execution validation — live solve wiring", () => {
  let dir: string;

  beforeEach(() => {
    dir = makeTempDir();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    rmSync(dir, { recursive: true, force: true });
  });

  it("routes the live codegen solve path through generateAndValidateScenarioSource", async () => {
    const codegen = await import("../src/scenarios/codegen/index.js");
    const { SolveManager } = await import("../src/knowledge/solver.js");
    const { DeterministicProvider } =
      await import("../src/providers/deterministic.js");

    const spy = vi.spyOn(codegen, "generateAndValidateScenarioSource");
    const manager = new SolveManager({
      provider: new DeterministicProvider(),
      store: {} as never,
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });

    const job: Record<string, unknown> = {
      jobId: "job_1",
      description: "deploy a tiny service",
      generations: 1,
      status: "pending",
    };

    const created = {
      name: "saved_sim",
      family: "simulation",
      spec: {
        description: "Deploy a tiny service",
        environment_description: "Test environment",
        initial_state_description: "Nothing is deployed yet",
        success_criteria: ["service deployed"],
        failure_modes: ["timeout"],
        max_steps: 5,
        actions: [
          {
            name: "provision",
            description: "Provision infrastructure",
            parameters: {},
            preconditions: [],
            effects: ["infra_ready"],
          },
          {
            name: "deploy",
            description: "Deploy the service",
            parameters: {},
            preconditions: ["provision"],
            effects: ["service_ready"],
          },
        ],
      },
    };

    await (
      manager as unknown as {
        runCodegenScenario: (
          job: Record<string, unknown>,
          created: typeof created,
          family: "simulation",
        ) => Promise<void>;
      }
    ).runCodegenScenario(job, created, "simulation");

    expect(spy).toHaveBeenCalledOnce();
    expect(spy).toHaveBeenCalledWith("simulation", created.spec, "saved_sim");
    expect(
      existsSync(
        join(dir, "knowledge", "_custom_scenarios", "saved_sim", "scenario.js"),
      ),
    ).toBe(true);
    expect(job.status).toBe("completed");
  });
});
