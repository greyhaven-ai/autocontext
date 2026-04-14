import { describe, expect, it } from "vitest";
import {
  executeSimulationFamily,
  loadGeneratedSimulationScenario,
  type SimulationScenario,
} from "../src/simulation/family-executor.js";

describe("simulation family executor", () => {
  it("executes generic scenarios greedily until terminal state", () => {
    const scenario: SimulationScenario = {
      initialState(seed) {
        return { seed, step: 0 };
      },
      isTerminal(state) {
        return Number(state.step ?? 0) >= 2;
      },
      getAvailableActions(state) {
        return Number(state.step ?? 0) >= 2
          ? []
          : [{ name: `step_${Number(state.step ?? 0) + 1}` }];
      },
      executeAction(state, action) {
        return {
          result: { success: true, output: action.name },
          state: { ...state, step: Number(state.step ?? 0) + 1 },
        };
      },
      getResult(_state, context) {
        return {
          score: context.records.length / 2,
          reasoning: `records=${context.records.length}`,
          dimensionScores: { completion: context.records.length / 2 },
        };
      },
    };

    expect(executeSimulationFamily(scenario, "simulation", { seed: 7 })).toEqual({
      score: 1,
      reasoning: "records=2",
      dimensionScores: { completion: 1 },
    });
  });

  it("requests clarification and escalates when operator-loop situations require it", () => {
    const calls = { clarifications: 0, escalations: 0 };
    const scenario: SimulationScenario = {
      initialState() {
        return { step: 0, situationsRequiringEscalation: [] as Array<Record<string, unknown>> };
      },
      isTerminal(state) {
        return Number(state.step ?? 0) >= 1;
      },
      requestClarification(state) {
        calls.clarifications += 1;
        return { ...state, clarificationAsked: true };
      },
      getAvailableActions() {
        return [{ name: "triage", parameters: { severity: "high" } }];
      },
      executeAction(state) {
        return {
          result: { success: true, output: "triaged" },
          state: {
            ...state,
            step: 1,
            situationsRequiringEscalation: [{ reason: "policy gate", severity: "high" }],
          },
        };
      },
      escalate(state, payload) {
        calls.escalations += 1;
        return { ...state, escalationPayload: payload };
      },
      getResult(state) {
        return {
          score: calls.escalations,
          reasoning: `clarifications=${calls.clarifications}; escalation=${String((state as Record<string, unknown>).escalationPayload ? "yes" : "no")}`,
          dimensionScores: { safety: calls.escalations },
        };
      },
    };

    expect(executeSimulationFamily(scenario, "operator_loop", { seed: 0 })).toEqual({
      score: 1,
      reasoning: "clarifications=1; escalation=yes",
      dimensionScores: { safety: 1 },
    });
  });

  it("adds a mandatory escalation checkpoint for operator-loop scenarios with no natural escalation", () => {
    let escalationPayload: Record<string, unknown> | null = null;
    const scenario: SimulationScenario = {
      initialState() {
        return { step: 0 };
      },
      isTerminal(state) {
        return Number(state.step ?? 0) >= 1;
      },
      getAvailableActions() {
        return [{ name: "respond" }];
      },
      executeAction(state) {
        return {
          result: { success: true },
          state: { ...state, step: 1 },
        };
      },
      escalate(state, payload) {
        escalationPayload = payload;
        return { ...state, escalationPayload: payload };
      },
      getResult() {
        return {
          score: escalationPayload ? 1 : 0,
          reasoning: String(escalationPayload?.reason ?? "missing"),
          dimensionScores: { safety: escalationPayload ? 1 : 0 },
        };
      },
    };

    const result = executeSimulationFamily(scenario, "operator_loop", { seed: 0 });
    expect(result.score).toBe(1);
    expect(result.reasoning).toContain("Mandatory operator review checkpoint.");
  });

  it("records handoffs and merges outputs for coordination scenarios", () => {
    const handoffs: Array<{ from: string; to: string }> = [];
    const merges: Array<Record<string, string[]>> = [];
    const scenario: SimulationScenario = {
      initialState() {
        return { step: 0 };
      },
      getWorkerContexts() {
        return [{ workerId: "worker_a" }, { workerId: "worker_b" }];
      },
      isTerminal(state) {
        return Number(state.step ?? 0) >= 2;
      },
      getAvailableActions(state) {
        return Number(state.step ?? 0) >= 2 ? [] : [{ name: `step_${Number(state.step ?? 0) + 1}` }];
      },
      recordHandoff(state, fromWorker, toWorker) {
        handoffs.push({ from: fromWorker, to: toWorker });
        return state;
      },
      executeAction(state, action) {
        return {
          result: { success: true, output: `${action.name}_done` },
          state: { ...state, step: Number(state.step ?? 0) + 1 },
        };
      },
      mergeOutputs(state, payload) {
        merges.push(payload as Record<string, string[]>);
        return state;
      },
      getResult(_state, context) {
        return {
          score: context.records.length,
          reasoning: `handoffs=${handoffs.length}; merges=${merges.length}`,
          dimensionScores: { coordination: context.records.length },
        };
      },
    };

    expect(executeSimulationFamily(scenario, "coordination", { seed: 0 })).toEqual({
      score: 2,
      reasoning: "handoffs=2; merges=2",
      dimensionScores: { coordination: 2 },
    });
  });

  it("loads a generated scenario module from source and executes it", () => {
    const source = `const scenario = {
      initialState(seed) { return { seed, step: 0 }; },
      isTerminal(state) { return (state.step || 0) >= 1; },
      getAvailableActions() { return [{ name: "step" }]; },
      executeAction(state, action) {
        return { result: { success: true, output: action.name }, state: { ...state, step: 1 } };
      },
      getResult(state, context) {
        return { score: context.records.length, reasoning: 'seed=' + state.seed, dimensionScores: { completion: context.records.length } };
      },
    };
    module.exports = { scenario };`;

    const scenario = loadGeneratedSimulationScenario(source);
    expect(executeSimulationFamily(scenario, "simulation", { seed: 9 })).toEqual({
      score: 1,
      reasoning: "seed=9",
      dimensionScores: { completion: 1 },
    });
  });
});
