/**
 * Tests for AC-343 Tasks 5-6: ScenarioInterface + Grid CTF scenario.
 */

import { describe, it, expect } from "vitest";

// ---------------------------------------------------------------------------
// Observation / Result / ReplayEnvelope Zod schemas
// ---------------------------------------------------------------------------

describe("Scenario data types", () => {
  it("should export ObservationSchema", async () => {
    const { ObservationSchema } = await import("../src/scenarios/game-interface.js");
    const obs = ObservationSchema.parse({
      narrative: "Player sees the board",
      state: { x: 1 },
      constraints: ["must move"],
    });
    expect(obs.narrative).toBe("Player sees the board");
    expect(obs.state.x).toBe(1);
    expect(obs.constraints).toEqual(["must move"]);
  });

  it("ObservationSchema should have defaults", async () => {
    const { ObservationSchema } = await import("../src/scenarios/game-interface.js");
    const obs = ObservationSchema.parse({ narrative: "test" });
    expect(obs.state).toEqual({});
    expect(obs.constraints).toEqual([]);
  });

  it("should export ResultSchema", async () => {
    const { ResultSchema } = await import("../src/scenarios/game-interface.js");
    const result = ResultSchema.parse({
      score: 0.75,
      winner: "challenger",
      summary: "GridCTF score 0.75",
    });
    expect(result.score).toBe(0.75);
    expect(result.winner).toBe("challenger");
    expect(result.passedValidation).toBe(true);
  });

  it("ResultSchema passedValidation false when errors present", async () => {
    const { ResultSchema } = await import("../src/scenarios/game-interface.js");
    const result = ResultSchema.parse({
      score: 0.0,
      summary: "fail",
      validationErrors: ["bad field"],
    });
    expect(result.passedValidation).toBe(false);
  });

  it("should export ReplayEnvelopeSchema", async () => {
    const { ReplayEnvelopeSchema } = await import("../src/scenarios/game-interface.js");
    const env = ReplayEnvelopeSchema.parse({
      scenario: "grid_ctf",
      seed: 42,
      narrative: "game played",
    });
    expect(env.scenario).toBe("grid_ctf");
    expect(env.timeline).toEqual([]);
  });

  it("should export ExecutionLimitsSchema", async () => {
    const { ExecutionLimitsSchema } = await import("../src/scenarios/game-interface.js");
    const limits = ExecutionLimitsSchema.parse({});
    expect(limits.timeoutSeconds).toBe(10.0);
    expect(limits.maxMemoryMb).toBe(512);
    expect(limits.networkAccess).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// ScenarioInterface type
// ---------------------------------------------------------------------------

describe("ScenarioInterface", () => {
  it("should export ScenarioInterface type", async () => {
    const mod = await import("../src/scenarios/game-interface.js");
    // ScenarioInterface is a TypeScript interface — we verify by checking
    // that the module exports the expected symbols
    expect(mod.ObservationSchema).toBeDefined();
    expect(mod.ResultSchema).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// GridCtfScenario
// ---------------------------------------------------------------------------

describe("GridCtfScenario", () => {
  it("should be importable", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    expect(GridCtfScenario).toBeDefined();
  });

  it("should have name 'grid_ctf'", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    expect(scenario.name).toBe("grid_ctf");
  });

  it("describeRules returns non-empty string", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const rules = scenario.describeRules();
    expect(rules.length).toBeGreaterThan(0);
    expect(rules).toContain("20x20");
  });

  it("describeStrategyInterface returns JSON schema description", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const desc = scenario.describeStrategyInterface();
    expect(desc).toContain("aggression");
    expect(desc).toContain("defense");
    expect(desc).toContain("path_bias");
  });

  it("scoringDimensions returns 3 dimensions", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const dims = scenario.scoringDimensions();
    expect(dims).toHaveLength(3);
    expect(dims![0].name).toBe("capture_progress");
    expect(dims![0].weight).toBe(0.6);
    expect(dims![1].name).toBe("defender_survival");
    expect(dims![2].name).toBe("energy_efficiency");
  });

  it("initialState is deterministic with seed", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const s1 = scenario.initialState(42);
    const s2 = scenario.initialState(42);
    expect(s1).toEqual(s2);
    expect(s1.seed).toBe(42);
    expect(s1.terminal).toBe(false);
    expect(s1.turn).toBe(0);
    expect(typeof s1.enemy_spawn_bias).toBe("number");
    expect(typeof s1.resource_density).toBe("number");
  });

  it("initialState varies with different seeds", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const s1 = scenario.initialState(1);
    const s2 = scenario.initialState(2);
    // Very unlikely to be identical
    expect(s1.enemy_spawn_bias !== s2.enemy_spawn_bias || s1.resource_density !== s2.resource_density).toBe(true);
  });

  it("validateActions accepts valid strategy", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const state = scenario.initialState(42);
    const [valid, msg] = scenario.validateActions(state, "challenger", {
      aggression: 0.6,
      defense: 0.4,
      path_bias: 0.5,
    });
    expect(valid).toBe(true);
    expect(msg).toBe("ok");
  });

  it("validateActions rejects missing field", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const state = scenario.initialState(42);
    const [valid, msg] = scenario.validateActions(state, "challenger", {
      aggression: 0.6,
      defense: 0.4,
    });
    expect(valid).toBe(false);
    expect(msg).toContain("path_bias");
  });

  it("validateActions rejects out-of-range value", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const state = scenario.initialState(42);
    const [valid, msg] = scenario.validateActions(state, "challenger", {
      aggression: 1.5,
      defense: 0.4,
      path_bias: 0.5,
    });
    expect(valid).toBe(false);
    expect(msg).toContain("[0,1]");
  });

  it("validateActions rejects aggression + defense > 1.4", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const state = scenario.initialState(42);
    const [valid, msg] = scenario.validateActions(state, "challenger", {
      aggression: 0.9,
      defense: 0.6,
      path_bias: 0.5,
    });
    expect(valid).toBe(false);
    expect(msg).toContain("1.4");
  });

  it("step produces terminal state with score", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const state = scenario.initialState(42);
    const next = scenario.step(state, { aggression: 0.6, defense: 0.4, path_bias: 0.5 });
    expect(next.terminal).toBe(true);
    expect(next.turn).toBe(1);
    expect(typeof next.score).toBe("number");
    expect(next.score).toBeGreaterThanOrEqual(0);
    expect(next.score).toBeLessThanOrEqual(1);
    expect(next.metrics).toBeDefined();
    expect(typeof next.metrics.capture_progress).toBe("number");
  });

  it("step is deterministic with same seed", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const s1 = scenario.step(scenario.initialState(42), { aggression: 0.6, defense: 0.4, path_bias: 0.5 });
    const s2 = scenario.step(scenario.initialState(42), { aggression: 0.6, defense: 0.4, path_bias: 0.5 });
    expect(s1.score).toBe(s2.score);
  });

  it("isTerminal returns false for initial state", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    expect(scenario.isTerminal(scenario.initialState(42))).toBe(false);
  });

  it("isTerminal returns true after step", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const next = scenario.step(scenario.initialState(42), { aggression: 0.6, defense: 0.4, path_bias: 0.5 });
    expect(scenario.isTerminal(next)).toBe(true);
  });

  it("getResult returns Result with winner", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const next = scenario.step(scenario.initialState(42), { aggression: 0.8, defense: 0.3, path_bias: 0.7 });
    const result = scenario.getResult(next);
    expect(result.score).toBe(next.score);
    expect(["challenger", "incumbent"]).toContain(result.winner);
    expect(result.summary).toContain("GridCTF");
  });

  it("winner is challenger when score >= 0.55", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    // High aggression + path_bias should produce high score
    const next = scenario.step(scenario.initialState(1000), { aggression: 0.9, defense: 0.3, path_bias: 0.9 });
    if (next.score >= 0.55) {
      const result = scenario.getResult(next);
      expect(result.winner).toBe("challenger");
    }
  });

  it("executeMatch runs full pipeline", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const result = scenario.executeMatch({ aggression: 0.6, defense: 0.4, path_bias: 0.5 }, 42);
    expect(result.score).toBeGreaterThanOrEqual(0);
    expect(result.score).toBeLessThanOrEqual(1);
    expect(result.summary).toContain("GridCTF");
    expect(result.replay.length).toBeGreaterThan(0);
  });

  it("executeMatch rejects invalid strategy", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const result = scenario.executeMatch({ aggression: 2.0, defense: 0.4, path_bias: 0.5 }, 42);
    expect(result.score).toBe(0.0);
    expect(result.winner).toBe("incumbent");
    expect(result.passedValidation).toBe(false);
  });

  it("enumerateLegalActions returns parameter descriptors", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const actions = scenario.enumerateLegalActions(scenario.initialState(42));
    expect(actions).toHaveLength(3);
    expect(actions![0].action).toBe("aggression");
    expect(actions![0].type).toBe("continuous");
  });

  it("enumerateLegalActions returns empty for terminal state", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const next = scenario.step(scenario.initialState(42), { aggression: 0.6, defense: 0.4, path_bias: 0.5 });
    const actions = scenario.enumerateLegalActions(next);
    expect(actions).toEqual([]);
  });

  it("replayToNarrative generates text", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const next = scenario.step(scenario.initialState(42), { aggression: 0.6, defense: 0.4, path_bias: 0.5 });
    const text = scenario.replayToNarrative(next.timeline);
    expect(text.length).toBeGreaterThan(0);
    expect(text).toContain("Capture");
  });

  it("getObservation returns Observation", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const state = scenario.initialState(42);
    const obs = scenario.getObservation(state, "challenger");
    expect(obs.narrative).toContain("challenger");
    expect(obs.state.enemy_spawn_bias).toBeDefined();
    expect(obs.constraints.length).toBeGreaterThan(0);
  });

  it("renderFrame returns frame data", async () => {
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const scenario = new GridCtfScenario();
    const state = scenario.initialState(42);
    const frame = scenario.renderFrame(state);
    expect(frame.scenario).toBe("grid_ctf");
    expect(frame.turn).toBe(0);
  });
});
