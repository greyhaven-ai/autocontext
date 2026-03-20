/**
 * Tests for AC-343 Tasks 7-9: Scenario Registry, Elo scoring,
 * Execution Supervisor, and Tournament Runner.
 */

import { describe, it, expect } from "vitest";

// ---------------------------------------------------------------------------
// Elo scoring (Task 8)
// ---------------------------------------------------------------------------

describe("Elo scoring", () => {
  it("should export expectedScore and updateElo", async () => {
    const { expectedScore, updateElo } = await import("../src/execution/elo.js");
    expect(typeof expectedScore).toBe("function");
    expect(typeof updateElo).toBe("function");
  });

  it("expectedScore returns 0.5 for equal ratings", async () => {
    const { expectedScore } = await import("../src/execution/elo.js");
    expect(expectedScore(1000, 1000)).toBeCloseTo(0.5);
  });

  it("expectedScore returns > 0.5 for higher player rating", async () => {
    const { expectedScore } = await import("../src/execution/elo.js");
    expect(expectedScore(1200, 1000)).toBeGreaterThan(0.5);
  });

  it("expectedScore returns < 0.5 for lower player rating", async () => {
    const { expectedScore } = await import("../src/execution/elo.js");
    expect(expectedScore(800, 1000)).toBeLessThan(0.5);
  });

  it("expectedScore(a,b) + expectedScore(b,a) ≈ 1.0", async () => {
    const { expectedScore } = await import("../src/execution/elo.js");
    const a = expectedScore(1200, 1000);
    const b = expectedScore(1000, 1200);
    expect(a + b).toBeCloseTo(1.0);
  });

  it("updateElo increases rating on win (actual=1)", async () => {
    const { updateElo } = await import("../src/execution/elo.js");
    const newRating = updateElo(1000, 1000, 1.0);
    expect(newRating).toBeGreaterThan(1000);
  });

  it("updateElo decreases rating on loss (actual=0)", async () => {
    const { updateElo } = await import("../src/execution/elo.js");
    const newRating = updateElo(1000, 1000, 0.0);
    expect(newRating).toBeLessThan(1000);
  });

  it("updateElo unchanged on draw at equal ratings (actual=0.5)", async () => {
    const { updateElo } = await import("../src/execution/elo.js");
    const newRating = updateElo(1000, 1000, 0.5);
    expect(newRating).toBeCloseTo(1000);
  });

  it("updateElo uses k_factor=24 by default", async () => {
    const { updateElo } = await import("../src/execution/elo.js");
    // Win from equal ratings: delta = k * (1 - 0.5) = 24 * 0.5 = 12
    const newRating = updateElo(1000, 1000, 1.0);
    expect(newRating).toBeCloseTo(1012);
  });

  it("updateElo accepts custom k_factor", async () => {
    const { updateElo } = await import("../src/execution/elo.js");
    const newRating = updateElo(1000, 1000, 1.0, 32);
    expect(newRating).toBeCloseTo(1016);
  });
});

// ---------------------------------------------------------------------------
// Scenario Registry (Task 7)
// ---------------------------------------------------------------------------

describe("Scenario Registry", () => {
  it("should export SCENARIO_REGISTRY", async () => {
    const { SCENARIO_REGISTRY } = await import("../src/scenarios/registry.js");
    expect(SCENARIO_REGISTRY).toBeDefined();
    expect(typeof SCENARIO_REGISTRY).toBe("object");
  });

  it("should register grid_ctf", async () => {
    const { SCENARIO_REGISTRY } = await import("../src/scenarios/registry.js");
    expect(SCENARIO_REGISTRY.grid_ctf).toBeDefined();
  });

  it("isGameScenario returns true for ScenarioInterface instance", async () => {
    const { isGameScenario, SCENARIO_REGISTRY } = await import("../src/scenarios/registry.js");
    const scenario = new SCENARIO_REGISTRY.grid_ctf();
    expect(isGameScenario(scenario)).toBe(true);
  });

  it("isAgentTask returns false for ScenarioInterface instance", async () => {
    const { isAgentTask, SCENARIO_REGISTRY } = await import("../src/scenarios/registry.js");
    const scenario = new SCENARIO_REGISTRY.grid_ctf();
    expect(isAgentTask(scenario)).toBe(false);
  });

  it("isGameScenario returns false for plain object", async () => {
    const { isGameScenario } = await import("../src/scenarios/registry.js");
    expect(isGameScenario({ name: "fake" })).toBe(false);
  });

  it("isAgentTask returns true for AgentTaskInterface-like object", async () => {
    const { isAgentTask } = await import("../src/scenarios/registry.js");
    const mock = {
      getTaskPrompt: () => "prompt",
      evaluateOutput: async () => ({ score: 0.5, reasoning: "ok", dimensionScores: {} }),
      getRubric: () => "rubric",
      initialState: () => ({}),
      describeTask: () => "task",
    };
    expect(isAgentTask(mock)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Execution Supervisor (Task 8b)
// ---------------------------------------------------------------------------

describe("ExecutionSupervisor", () => {
  it("should be importable", async () => {
    const { ExecutionSupervisor } = await import("../src/execution/supervisor.js");
    expect(ExecutionSupervisor).toBeDefined();
  });

  it("run executes a match via scenario.executeMatch", async () => {
    const { ExecutionSupervisor } = await import("../src/execution/supervisor.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const supervisor = new ExecutionSupervisor();
    const scenario = new GridCtfScenario();
    const output = supervisor.run(scenario, {
      strategy: { aggression: 0.6, defense: 0.4, path_bias: 0.5 },
      seed: 42,
      limits: { timeoutSeconds: 10, maxMemoryMb: 512, networkAccess: false },
    });
    expect(output.result.score).toBeGreaterThanOrEqual(0);
    expect(output.result.score).toBeLessThanOrEqual(1);
    expect(output.replay).toBeDefined();
    expect(output.replay.scenario).toBe("grid_ctf");
  });

  it("run propagates validation errors", async () => {
    const { ExecutionSupervisor } = await import("../src/execution/supervisor.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const supervisor = new ExecutionSupervisor();
    const scenario = new GridCtfScenario();
    const output = supervisor.run(scenario, {
      strategy: { aggression: 2.0, defense: 0.4, path_bias: 0.5 },
      seed: 42,
      limits: { timeoutSeconds: 10, maxMemoryMb: 512, networkAccess: false },
    });
    expect(output.result.score).toBe(0.0);
    expect(output.result.passedValidation).toBe(false);
  });

  it("delegates execution through the injected executor", async () => {
    const { ExecutionSupervisor } = await import("../src/execution/supervisor.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const calls: Array<Record<string, unknown>> = [];
    const supervisor = new ExecutionSupervisor({
      execute(_scenario, strategy, seed, limits) {
        calls.push({ strategy, seed, limits });
        return {
          result: {
            score: 0.7,
            winner: "challenger",
            summary: "ok",
            replay: [],
            metrics: {},
            validationErrors: [],
            passedValidation: true,
          },
          replay: {
            scenario: "grid_ctf",
            seed,
            narrative: "ok",
            timeline: [],
          },
        };
      },
    });

    const scenario = new GridCtfScenario();
    const output = supervisor.run(scenario, {
      strategy: { aggression: 0.6, defense: 0.4, path_bias: 0.5 },
      seed: 9,
      limits: { timeoutSeconds: 2, maxMemoryMb: 64, networkAccess: false },
    });

    expect(calls).toHaveLength(1);
    expect(calls[0]).toEqual({
      strategy: { aggression: 0.6, defense: 0.4, path_bias: 0.5 },
      seed: 9,
      limits: { timeoutSeconds: 2, maxMemoryMb: 64, networkAccess: false },
    });
    expect(output.result.score).toBe(0.7);
  });
});

// ---------------------------------------------------------------------------
// Tournament Runner (Task 9)
// ---------------------------------------------------------------------------

describe("TournamentRunner", () => {
  it("should be importable", async () => {
    const { TournamentRunner } = await import("../src/execution/tournament.js");
    expect(TournamentRunner).toBeDefined();
  });

  it("runs N matches and returns aggregated results", async () => {
    const { TournamentRunner } = await import("../src/execution/tournament.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const scenario = new GridCtfScenario();
    const runner = new TournamentRunner(scenario, { matchCount: 3, seedBase: 1000 });
    const result = runner.run({ aggression: 0.6, defense: 0.4, path_bias: 0.5 });

    expect(result.matches).toHaveLength(3);
    expect(typeof result.meanScore).toBe("number");
    expect(typeof result.bestScore).toBe("number");
    expect(result.bestScore).toBeGreaterThanOrEqual(result.meanScore);
    expect(typeof result.wins).toBe("number");
    expect(typeof result.losses).toBe("number");
    expect(result.wins + result.losses).toBe(3);
  });

  it("computes Elo rating", async () => {
    const { TournamentRunner } = await import("../src/execution/tournament.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const scenario = new GridCtfScenario();
    const runner = new TournamentRunner(scenario, { matchCount: 5, seedBase: 1000 });
    const result = runner.run({ aggression: 0.7, defense: 0.3, path_bias: 0.6 });

    expect(typeof result.elo).toBe("number");
    // Elo starts at 1000 and should move based on results
    expect(result.elo).not.toBe(1000);
  });

  it("routes tournament matches through the execution supervisor with limits", async () => {
    const { TournamentRunner } = await import("../src/execution/tournament.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const scenario = new GridCtfScenario();
    const calls: Array<Record<string, unknown>> = [];
    const supervisor = {
      run(_scenario: unknown, payload: Record<string, unknown>) {
        calls.push(payload);
        return {
          result: {
            score: 0.72,
            winner: "challenger",
            summary: "ok",
            replay: [],
            metrics: {},
            validationErrors: [],
            passedValidation: true,
          },
          replay: {
            scenario: "grid_ctf",
            seed: payload.seed,
            narrative: "from envelope",
            timeline: [{ event: "from-envelope" }],
          },
        };
      },
    };

    const runner = new TournamentRunner(
      scenario,
      {
        matchCount: 1,
        seedBase: 77,
        limits: { timeoutSeconds: 3, maxMemoryMb: 128, networkAccess: false },
      },
      supervisor,
    );
    const result = runner.run({ aggression: 0.6, defense: 0.4, path_bias: 0.5 });

    expect(calls).toHaveLength(1);
    expect(calls[0].seed).toBe(77);
    expect(calls[0].limits).toEqual({
      timeoutSeconds: 3,
      maxMemoryMb: 128,
      networkAccess: false,
    });
    expect(result.matches[0].replay).toEqual([{ event: "from-envelope" }]);
  });

  it("uses continuous match scores for Elo updates", async () => {
    const { TournamentRunner } = await import("../src/execution/tournament.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const scenario = new GridCtfScenario();
    const makeSupervisor = (score: number) => ({
      run() {
        return {
          result: {
            score,
            winner: "challenger",
            summary: "ok",
            replay: [],
            metrics: {},
            validationErrors: [],
            passedValidation: true,
          },
          replay: {
            scenario: "grid_ctf",
            seed: 0,
            narrative: "ok",
            timeline: [],
          },
        };
      },
    });

    const nearThreshold = new TournamentRunner(
      scenario,
      { matchCount: 1, seedBase: 1 },
      makeSupervisor(0.56),
    ).run({ aggression: 0.6, defense: 0.4, path_bias: 0.5 });
    const strongWin = new TournamentRunner(
      scenario,
      { matchCount: 1, seedBase: 1 },
      makeSupervisor(0.96),
    ).run({ aggression: 0.6, defense: 0.4, path_bias: 0.5 });

    expect(strongWin.elo).toBeGreaterThan(nearThreshold.elo);
  });

  it("each match has correct seed", async () => {
    const { TournamentRunner } = await import("../src/execution/tournament.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const scenario = new GridCtfScenario();
    const runner = new TournamentRunner(scenario, { matchCount: 3, seedBase: 2000 });
    const result = runner.run({ aggression: 0.5, defense: 0.5, path_bias: 0.5 });

    expect(result.matches[0].seed).toBe(2000);
    expect(result.matches[1].seed).toBe(2001);
    expect(result.matches[2].seed).toBe(2002);
  });

  it("is deterministic with same seeds", async () => {
    const { TournamentRunner } = await import("../src/execution/tournament.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const scenario = new GridCtfScenario();
    const strategy = { aggression: 0.6, defense: 0.4, path_bias: 0.5 };

    const r1 = new TournamentRunner(scenario, { matchCount: 3, seedBase: 1000 }).run(strategy);
    const r2 = new TournamentRunner(scenario, { matchCount: 3, seedBase: 1000 }).run(strategy);

    expect(r1.meanScore).toBe(r2.meanScore);
    expect(r1.bestScore).toBe(r2.bestScore);
    expect(r1.elo).toBe(r2.elo);
  });

  it("match results include per-match scores", async () => {
    const { TournamentRunner } = await import("../src/execution/tournament.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const scenario = new GridCtfScenario();
    const runner = new TournamentRunner(scenario, { matchCount: 2, seedBase: 1000 });
    const result = runner.run({ aggression: 0.6, defense: 0.4, path_bias: 0.5 });

    for (const match of result.matches) {
      expect(typeof match.score).toBe("number");
      expect(typeof match.seed).toBe("number");
      expect(typeof match.passedValidation).toBe("boolean");
      expect(match.winner).toBeDefined();
    }
  });

  it("handles invalid strategy gracefully", async () => {
    const { TournamentRunner } = await import("../src/execution/tournament.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const scenario = new GridCtfScenario();
    const runner = new TournamentRunner(scenario, { matchCount: 2, seedBase: 1000 });
    const result = runner.run({ aggression: 2.0, defense: 0.4, path_bias: 0.5 });

    expect(result.meanScore).toBe(0.0);
    expect(result.wins).toBe(0);
    expect(result.losses).toBe(2);
  });
});
