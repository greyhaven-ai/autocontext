import { describe, expect, it } from "vitest";

import type { CustomScenarioEntry } from "../src/scenarios/custom-loader.js";
import type { ScenarioInterface } from "../src/scenarios/game-interface.js";
import {
  buildEnvironmentInfo,
  describeCustomScenarioEntry,
} from "../src/server/run-environment-catalog.js";

class FakeGameScenario implements ScenarioInterface {
  readonly name = "grid_ctf";
  describeRules(): string { return "Capture the flag rules"; }
  describeStrategyInterface(): string { return "Strategy"; }
  describeEvaluationCriteria(): string { return "Criteria"; }
  initialState(): Record<string, unknown> { return {}; }
  getObservation() { return { narrative: "obs", state: {}, constraints: [] }; }
  validateActions(): [boolean, string] { return [true, "ok"]; }
  step(): Record<string, unknown> { return {}; }
  isTerminal(): boolean { return true; }
  getResult() {
    return {
      score: 1,
      winner: null,
      summary: "done",
      replay: [],
      metrics: {},
      validationErrors: [],
      get passedValidation() { return true; },
    };
  }
  replayToNarrative(): string { return "narrative"; }
  renderFrame(): Record<string, unknown> { return {}; }
  enumerateLegalActions() { return null; }
  scoringDimensions() { return null; }
  executeMatch() {
    return {
      score: 1,
      winner: null,
      summary: "done",
      replay: [],
      metrics: {},
      validationErrors: [],
      get passedValidation() { return true; },
    };
  }
}

describe("run environment catalog", () => {
  it("describes custom scenarios according to run support", () => {
    const agentTask: CustomScenarioEntry = {
      name: "saved_task",
      type: "agent_task",
      spec: { taskPrompt: "Summarize incidents." },
      path: "/tmp/saved_task",
      hasGeneratedSource: false,
    };
    const generated: CustomScenarioEntry = {
      name: "saved_sim",
      type: "simulation",
      spec: { description: "Saved simulation" },
      path: "/tmp/saved_sim",
      hasGeneratedSource: true,
    };

    expect(describeCustomScenarioEntry(agentTask)).toContain("not runnable via /run yet");
    expect(describeCustomScenarioEntry(generated)).toContain("runnable via /run");
  });

  it("builds environment info from built-in and custom scenario catalogs", () => {
    const info = buildEnvironmentInfo({
      builtinScenarioNames: ["grid_ctf"],
      getBuiltinScenarioClass: () => FakeGameScenario,
      customScenarios: new Map([
        [
          "saved_task",
          {
            name: "saved_task",
            type: "agent_task",
            spec: { taskPrompt: "Summarize incidents." },
            path: "/tmp/saved_task",
            hasGeneratedSource: false,
          } satisfies CustomScenarioEntry,
        ],
      ]),
      activeProviderType: "deterministic",
    });

    expect(info.currentExecutor).toBe("local");
    expect(info.agentProvider).toBe("deterministic");
    expect(info.scenarios).toEqual([
      { name: "grid_ctf", description: "Capture the flag rules" },
      expect.objectContaining({ name: "saved_task" }),
    ]);
  });
});
