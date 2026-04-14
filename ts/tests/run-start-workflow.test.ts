import { describe, expect, it, vi } from "vitest";

import type { AppSettings } from "../src/config/index.js";
import type { CustomScenarioEntry } from "../src/scenarios/custom-loader.js";
import type { ScenarioFamilyName } from "../src/scenarios/families.js";
import type { RoleProviderBundle } from "../src/providers/index.js";
import {
  executeBuiltInGameStartRun,
  executeGeneratedCustomStartRun,
  resolveRunStartPlan,
} from "../src/server/run-start-workflow.js";

function makeSettings(): AppSettings {
  return {
    ...({} as AppSettings),
    matchesPerGeneration: 3,
    maxRetries: 2,
    backpressureMinDelta: 0.01,
    playbookMaxVersions: 5,
    contextBudgetTokens: 32000,
    curatorEnabled: true,
    curatorConsolidateEveryNGens: 3,
    skillMaxLessons: 30,
    deadEndTrackingEnabled: true,
    deadEndMaxEntries: 25,
    stagnationResetEnabled: true,
    stagnationRollbackThreshold: 5,
    stagnationPlateauWindow: 3,
    stagnationPlateauEpsilon: 0.01,
    stagnationDistillTopLessons: 5,
    explorationMode: "linear",
    notifyWebhookUrl: null,
    notifyOn: "completion",
  };
}

describe("run start workflow", () => {
  it("resolves built-in game runs from the registry", () => {
    const plan = resolveRunStartPlan({
      scenario: "grid_ctf",
      builtinScenarioNames: ["grid_ctf"],
    });

    expect(plan).toEqual({ kind: "builtin_game", scenarioName: "grid_ctf" });
  });

  it("resolves generated custom runs when saved source and a runnable family exist", () => {
    const entry: CustomScenarioEntry = {
      name: "saved_sim",
      type: "simulation",
      spec: { description: "Saved simulation" },
      path: "/tmp/saved_sim",
      hasGeneratedSource: true,
    };

    const plan = resolveRunStartPlan({
      scenario: "saved_sim",
      builtinScenarioNames: ["grid_ctf"],
      customScenario: entry,
      customScenarioFamily: "simulation",
    });

    expect(plan).toEqual({
      kind: "generated_custom",
      scenarioName: "saved_sim",
      entry,
      family: "simulation",
    });
  });

  it("rejects saved custom agent-task scenarios for /run", () => {
    const entry: CustomScenarioEntry = {
      name: "saved_task",
      type: "agent_task",
      spec: { description: "Saved task" },
      path: "/tmp/saved_task",
      hasGeneratedSource: false,
    };

    expect(() => resolveRunStartPlan({
      scenario: "saved_task",
      builtinScenarioNames: ["grid_ctf"],
      customScenario: entry,
      customScenarioFamily: "agent_task",
    })).toThrow(/only built-in game scenarios and generated non-agent-task scenarios/i);
  });

  it("executes built-in game runs through the generation runner boundary", async () => {
    class FakeScenario {
      readonly name = "grid_ctf";
      describeRules() { return "Rules"; }
      describeStrategyInterface() { return "Strategy"; }
      describeEvaluationCriteria() { return "Criteria"; }
      initialState() { return {}; }
      getObservation() { return { narrative: "obs", state: {}, constraints: [] }; }
      validateActions() { return [true, "ok"] as [boolean, string]; }
      step() { return {}; }
      isTerminal() { return true; }
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
      replayToNarrative() { return "narrative"; }
      renderFrame() { return {}; }
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

    const migrate = vi.fn();
    const close = vi.fn();
    const store = { migrate, close };
    const run = vi.fn(async () => ({ generationsCompleted: 2 }));
    const createRunner = vi.fn(() => ({ run }));
    const bundle: RoleProviderBundle = {
      defaultProvider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
      defaultConfig: { providerType: "deterministic", apiKey: "", baseUrl: "", model: "test" },
      roleProviders: {},
      roleModels: {},
    };

    const result = await executeBuiltInGameStartRun({
      runId: "run_1",
      scenarioName: "grid_ctf",
      generations: 2,
      settings: makeSettings(),
      providerBundle: bundle,
      opts: {
        dbPath: "/tmp/test.db",
        migrationsDir: "/tmp/migrations",
        runsRoot: "/tmp/runs",
        knowledgeRoot: "/tmp/knowledge",
      },
      controller: { isPaused: () => false } as never,
      events: {} as never,
      deps: {
        resolveScenarioClass: () => FakeScenario as never,
        createStore: () => store as never,
        createRunner,
      },
    });

    expect(migrate).toHaveBeenCalledWith("/tmp/migrations");
    expect(run).toHaveBeenCalledWith("run_1", 2);
    expect(close).toHaveBeenCalledOnce();
    expect(result).toBeUndefined();
  });

  it("executes generated custom runs and emits generation lifecycle events", async () => {
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    const events = {
      emit: (event: string, payload: Record<string, unknown>) => {
        emitted.push({ event, payload });
      },
    };

    await executeGeneratedCustomStartRun({
      runId: "run_2",
      scenarioName: "saved_sim",
      entry: {
        name: "saved_sim",
        type: "simulation",
        spec: { max_steps: 3 },
        path: "/tmp/saved_sim",
        hasGeneratedSource: true,
      },
      family: "simulation",
      generations: 2,
      knowledgeRoot: "/tmp/knowledge",
      controller: { waitIfPaused: async () => {} } as never,
      events: events as never,
      deps: {
        executeGeneratedScenarioEntry: vi
          .fn()
          .mockResolvedValueOnce({
            family: "simulation" as ScenarioFamilyName,
            stepsExecuted: 2,
            finalState: {},
            records: [],
            score: 0.6,
            reasoning: "first generation",
            dimensionScores: {},
          })
          .mockResolvedValueOnce({
            family: "simulation" as ScenarioFamilyName,
            stepsExecuted: 3,
            finalState: {},
            records: [],
            score: 0.9,
            reasoning: "second generation",
            dimensionScores: {},
          }),
      },
    });

    expect(emitted[0]?.event).toBe("run_started");
    expect(emitted.filter((entry) => entry.event === "generation_started")).toHaveLength(2);
    expect(emitted.filter((entry) => entry.event === "generation_completed")).toHaveLength(2);
    const completed = emitted.find((entry) => entry.event === "run_completed");
    expect(completed?.payload.best_score).toBe(0.9);
    expect(completed?.payload.completed_generations).toBe(2);
  });
});
