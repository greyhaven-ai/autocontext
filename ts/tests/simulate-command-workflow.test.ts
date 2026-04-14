import { describe, expect, it } from "vitest";

import {
  SIMULATE_HELP_TEXT,
  ensurePresetPairing,
  planSimulateCommand,
  renderCompareSuccess,
  renderReplaySuccess,
  renderSimulationSuccess,
} from "../src/cli/simulate-command-workflow.js";
import type { SimulationCompareResult, SimulationResult } from "../src/simulation/types.js";

describe("simulate command workflow", () => {
  it("exposes simulate help text", () => {
    expect(SIMULATE_HELP_TEXT).toContain("autoctx simulate");
    expect(SIMULATE_HELP_TEXT).toContain("--replay <id>");
    expect(SIMULATE_HELP_TEXT).toContain("--compare-left <id>");
    expect(SIMULATE_HELP_TEXT).toContain("--preset-file <path>");
  });

  it("plans compare, replay, export, and run modes", () => {
    expect(planSimulateCommand({ "compare-left": "sim_a", "compare-right": "sim_b" })).toEqual({
      mode: "compare",
      compareLeft: "sim_a",
      compareRight: "sim_b",
      exportId: undefined,
      replayId: undefined,
      description: undefined,
    });

    expect(planSimulateCommand({ replay: "deploy_sim" }).mode).toBe("replay");
    expect(planSimulateCommand({ export: "deploy_sim" }).mode).toBe("export");
    expect(planSimulateCommand({ description: "simulate a deployment" }).mode).toBe("run");
  });

  it("rejects incomplete compare inputs and fully missing modes", () => {
    expect(() => planSimulateCommand({ "compare-left": "sim_a" })).toThrow(
      "Error: --compare-left and --compare-right must be provided together. Run 'autoctx simulate --help' for usage.",
    );

    expect(() => planSimulateCommand({})).toThrow(
      "Error: --description, --replay, --compare-left/--compare-right, or --export is required. Run 'autoctx simulate --help' for usage.",
    );
  });

  it("requires preset and preset-file together", () => {
    expect(() => ensurePresetPairing({ preset: "aggressive" })).toThrow(
      "Error: --preset and --preset-file must be provided together. Run 'autoctx simulate --help' for usage.",
    );

    expect(() =>
      ensurePresetPairing({ preset: "aggressive", "preset-file": "presets.json" }),
    ).not.toThrow();
  });

  it("renders simulation success output", () => {
    const result: SimulationResult = {
      id: "sim_123",
      name: "deploy_sim",
      family: "simulation",
      status: "completed",
      description: "simulate a deployment",
      assumptions: ["bounded to 10 steps"],
      variables: {},
      summary: {
        score: 0.82,
        reasoning: "Rollback was effective.",
        dimensionScores: { completion: 0.9 },
        mostSensitiveVariables: ["threshold"],
      },
      sweep: {
        dimensions: [{ name: "threshold", values: [0.4, 0.5, 0.6], scale: "linear" }],
        runs: 6,
        results: [],
      },
      artifacts: { scenarioDir: "/tmp/deploy_sim" },
      warnings: ["Model-driven result"],
    };

    expect(renderSimulationSuccess(result)).toBe([
      "Simulation: deploy_sim (family: simulation)",
      "Score: 0.82",
      "Reasoning: Rollback was effective.",
      "Sweep: 6 runs across 1 dimension(s)",
      "Most sensitive: threshold",
      "",
      "Assumptions:",
      "  - bounded to 10 steps",
      "",
      "Warnings:",
      "  ⚠ Model-driven result",
      "",
      "Artifacts: /tmp/deploy_sim",
    ].join("\n"));
  });

  it("renders replay and compare success output", () => {
    const replay: SimulationResult = {
      id: "sim_456",
      name: "deploy_sim",
      family: "simulation",
      status: "completed",
      description: "simulate a deployment",
      assumptions: [],
      variables: {},
      summary: { score: 0.79, reasoning: "Stable.", dimensionScores: {} },
      artifacts: { scenarioDir: "/tmp/deploy_sim" },
      warnings: [],
      originalScore: 0.74,
      scoreDelta: 0.05,
      replayOf: "deploy_sim",
    };
    expect(renderReplaySuccess(replay)).toBe([
      "Replay: deploy_sim (original score: 0.74, replay score: 0.79, delta: 0.0500)",
      "Artifacts: /tmp/deploy_sim",
    ].join("\n"));

    const compare: SimulationCompareResult = {
      status: "completed",
      left: { name: "sim_a", score: 0.52, variables: {} },
      right: { name: "sim_b", score: 0.83, variables: {} },
      scoreDelta: 0.31,
      variableDeltas: {},
      dimensionDeltas: {},
      likelyDrivers: ["threshold", "budget"],
      summary: "Threshold and budget improved recovery.",
    };
    expect(renderCompareSuccess(compare)).toBe([
      "Compare: sim_a vs sim_b",
      "Score: 0.52 → 0.83 (delta: 0.3100)",
      "Likely drivers: threshold, budget",
      "Threshold and budget improved recovery.",
    ].join("\n"));
  });
});
