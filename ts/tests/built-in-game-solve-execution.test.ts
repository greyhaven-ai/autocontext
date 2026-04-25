import { describe, expect, it, vi } from "vitest";

import type { ScenarioInterface } from "../src/scenarios/game-interface.js";
import { executeBuiltInGameSolve } from "../src/knowledge/built-in-game-solve-execution.js";

class FakeGameScenario implements ScenarioInterface {
  readonly name = "grid_ctf";

  describeRules(): string { return "Rules"; }
  describeStrategyInterface(): string { return "Strategy"; }
  describeEvaluationCriteria(): string { return "Criteria"; }
  initialState(): Record<string, unknown> { return {}; }
  getObservation(): { narrative: string; state: Record<string, unknown>; constraints: string[] } {
    return { narrative: "obs", state: {}, constraints: [] };
  }
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
      get passedValidation() {
        return true;
      },
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
      get passedValidation() {
        return true;
      },
    };
  }
}

describe("built-in game solve execution", () => {
  it("runs the generation workflow and exports the resulting package", async () => {
    const run = vi.fn(async () => ({
      runId: "solve_grid_ctf_job_1",
      generationsCompleted: 2,
      bestScore: 0.7,
      currentElo: 1510,
    }));
    const createRunner = vi.fn(() => ({ run }));
    const exportPackage = vi.fn(() => ({ scenario_name: "grid_ctf", skill_markdown: "# Playbook" }));

    const result = await executeBuiltInGameSolve({
      provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
      store: { marker: true } as never,
      runsRoot: "/tmp/runs",
      knowledgeRoot: "/tmp/knowledge",
      scenarioName: "grid_ctf",
      jobId: "job_1",
      generations: 2,
      generationTimeBudgetSeconds: 7,
      deps: {
        resolveScenarioClass: () => FakeGameScenario,
        createRunner,
        exportPackage,
      },
    });

    expect(createRunner).toHaveBeenCalledOnce();
    expect(createRunner).toHaveBeenCalledWith(
      expect.objectContaining({ generationTimeBudgetSeconds: 7 }),
    );
    expect(run).toHaveBeenCalledWith("solve_grid_ctf_job_1", 2);
    expect(exportPackage).toHaveBeenCalledOnce();
    expect(result.progress).toBe(2);
    expect(result.result.scenario_name).toBe("grid_ctf");
  });

  it("fails when the built-in game scenario is missing", async () => {
    await expect(
      executeBuiltInGameSolve({
        provider: { name: "test", defaultModel: () => "test", complete: vi.fn() },
        store: {} as never,
        runsRoot: "/tmp/runs",
        knowledgeRoot: "/tmp/knowledge",
        scenarioName: "missing_game",
        jobId: "job_2",
        generations: 1,
        deps: {
          resolveScenarioClass: () => undefined,
        },
      }),
    ).rejects.toThrow("Game scenario 'missing_game' not found in SCENARIO_REGISTRY");
  });
});
