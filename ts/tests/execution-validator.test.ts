/**
 * AC-530: Execution validator must reject hollow generated artifacts.
 *
 * Covers the two gaps:
 *   1. validateSimulationLike() accepted empty ACTIONS arrays.
 *   2. validateArtifactEditing() accepted empty initialArtifacts arrays.
 */

import { describe, it, expect } from "vitest";
import { validateGeneratedScenario } from "../src/scenarios/codegen/execution-validator.js";

describe("execution-validator catches hollow artifacts (AC-530)", () => {
  it("rejects simulation with empty ACTIONS array", async () => {
    const source = `
const ACTIONS = [];
const REQUIRED_ACTIONS = [];
const scenario = {
  name: "hollow_sim",
  describeScenario() { return "test"; },
  describeEnvironment() { return { name: "test", description: "", availableActions: [], initialStateDescription: "", successCriteria: [], failureModes: [] }; },
  initialState(seed) { return { seed: seed || 0, step: 0, completedActions: [], failedActions: [], timeline: [], terminal: false }; },
  getAvailableActions(state) { return ACTIONS.filter((a) => !new Set(state.completedActions || []).has(a.name)); },
  executeAction(state, action) { return { result: { success: false, output: "", stateChanges: {}, error: "unknown" }, state }; },
  isTerminal(state) { return true; },
  getResult(state, trace) { return { score: 0, reasoning: "empty", dimensionScores: {} }; },
  getRubric() { return "test rubric"; },
  maxSteps() { return 10; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "simulation",
      "hollow_sim",
    );
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes("at least one action"))).toBe(
      true,
    );
  });

  it("rejects artifact_editing with empty initialArtifacts", async () => {
    const source = `
const scenario = {
  name: "hollow_edit",
  describeTask() { return "edit something"; },
  getRubric() { return "test rubric"; },
  initialArtifacts() { return []; },
  getEditPrompt(artifacts, state) { return "edit this"; },
  validateArtifact(artifact) { return { valid: true, errors: [] }; },
  initialState(seed) { return { seed: seed || 0, step: 0 }; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "artifact_editing",
      "hollow_edit",
    );
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes("at least one artifact"))).toBe(
      true,
    );
  });

  it("accepts simulation with non-empty ACTIONS", async () => {
    const source = `
const ACTIONS = [{ name: "act1", description: "do thing", parameters: {}, preconditions: [], effects: [] }];
const REQUIRED_ACTIONS = ["act1"];
const scenario = {
  name: "valid_sim",
  describeScenario() { return "test"; },
  describeEnvironment() { return { name: "test", description: "", availableActions: ACTIONS, initialStateDescription: "", successCriteria: [], failureModes: [] }; },
  initialState(seed) { return { seed: seed || 0, step: 0, completedActions: [], failedActions: [], timeline: [], terminal: false }; },
  getAvailableActions(state) { return ACTIONS.filter((a) => !new Set(state.completedActions || []).has(a.name)); },
  executeAction(state, action) {
    const nextState = { ...state, completedActions: [...(state.completedActions || []), action.name] };
    return { result: { success: true, output: "done", stateChanges: {} }, state: nextState };
  },
  isTerminal(state) { return (state.completedActions || []).length >= ACTIONS.length; },
  getResult(state, trace) { return { score: 1, reasoning: "done", dimensionScores: { completion: 1 } }; },
  getRubric() { return "test rubric"; },
  maxSteps() { return 10; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "simulation",
      "valid_sim",
    );
    expect(result.valid).toBe(true);
  });

  it("accepts artifact_editing with non-empty initialArtifacts", async () => {
    const source = `
const scenario = {
  name: "valid_edit",
  describeTask() { return "edit something"; },
  getRubric() { return "test rubric"; },
  initialArtifacts() { return [{ name: "file.txt", content: "hello", format: "text" }]; },
  getEditPrompt(artifacts, state) { return "edit this"; },
  validateArtifact(artifact) { return { valid: true, errors: [] }; },
  initialState(seed) { return { seed: seed || 0, step: 0 }; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "artifact_editing",
      "valid_edit",
    );
    expect(result.valid).toBe(true);
  });

  it("rejects hollow scenarios for all simulation-like families", async () => {
    const families = [
      "simulation",
      "workflow",
      "operator_loop",
      "coordination",
    ];
    for (const family of families) {
      const source = `
const ACTIONS = [];
const REQUIRED_ACTIONS = [];
const scenario = {
  name: "hollow",
  describeScenario() { return "test"; },
  describeEnvironment() { return { name: "test", description: "", availableActions: [], initialStateDescription: "", successCriteria: [], failureModes: [] }; },
  initialState(seed) { return { seed: seed || 0, step: 0, completedActions: [], failedActions: [], timeline: [], terminal: false }; },
  getAvailableActions(state) { return []; },
  executeAction(state, action) { return { result: { success: false, output: "", stateChanges: {}, error: "nope" }, state }; },
  isTerminal(state) { return true; },
  getResult(state, trace) { return { score: 0, reasoning: "empty", dimensionScores: {} }; },
  getRubric() { return "test rubric"; },
  maxSteps() { return 10; },
};
module.exports = { scenario };
`;
      const result = await validateGeneratedScenario(
        source,
        family,
        `hollow_${family}`,
      );
      expect(result.valid, `${family} should reject hollow scenario`).toBe(
        false,
      );
    }
  });

  it("rejects operator_loop scenarios missing required intervention hooks", async () => {
    const source = `
const ACTIONS = [{ name: "inspect", description: "Inspect", parameters: {}, preconditions: [], effects: [] }];
const scenario = {
  name: "broken_op",
  describeScenario() { return "test"; },
  describeEnvironment() { return { name: "test", description: "", availableActions: ACTIONS, initialStateDescription: "", successCriteria: [], failureModes: [] }; },
  initialState(seed) { return { seed: seed || 0, step: 0, completedActions: [], situationsRequiringEscalation: [] }; },
  getAvailableActions() { return ACTIONS; },
  executeAction(state, action) { return { result: { success: true, output: "done" }, state: { ...state, completedActions: [action.name] } }; },
  isTerminal() { return true; },
  getResult() { return { score: 1, reasoning: "done", dimensionScores: {} }; },
  getRubric() { return "test rubric"; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "operator_loop",
      "broken_op",
    );
    expect(result.valid).toBe(false);
    expect(
      result.errors.some(
        (e) =>
          e.includes("missing required methods") &&
          e.includes("requestClarification") &&
          e.includes("escalate"),
      ),
    ).toBe(true);
  });

  it("accepts operator_loop scenarios with clarification and escalation hooks", async () => {
    const source = `
const ACTIONS = [{ name: "inspect", description: "Inspect", parameters: {}, preconditions: [], effects: [] }];
const scenario = {
  name: "valid_op",
  describeScenario() { return "test"; },
  describeEnvironment() { return { name: "test", description: "", availableActions: ACTIONS, initialStateDescription: "", successCriteria: [], failureModes: [] }; },
  initialState(seed) { return { seed: seed || 0, step: 0, completedActions: [], situationsRequiringEscalation: [] }; },
  getAvailableActions() { return ACTIONS; },
  executeAction(state, action) { return { result: { success: true, output: "done" }, state: { ...state, completedActions: [action.name] } }; },
  isTerminal() { return true; },
  getResult() { return { score: 1, reasoning: "done", dimensionScores: {} }; },
  getRubric() { return "test rubric"; },
  requestClarification(state, req) { return { ...state, clarificationRequest: req }; },
  escalate(state, event) { return { ...state, escalationEvent: event }; },
};
module.exports = { scenario };
`;
    const result = await validateGeneratedScenario(
      source,
      "operator_loop",
      "valid_op",
    );
    expect(result.valid).toBe(true);
    expect(result.executedMethods).toContain("requestClarification");
    expect(result.executedMethods).toContain("escalate");
  });
});
