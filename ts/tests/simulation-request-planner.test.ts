import { describe, expect, it } from "vitest";
import {
  buildSimulationExecutionConfig,
  collectReplayVariables,
  deriveSimulationName,
  inferSimulationFamily,
  resolveSimulationExecutionConfig,
} from "../src/simulation/request-planner.js";
import type { SimulationResult } from "../src/simulation/types.js";

describe("simulation request planner", () => {
  it("derives a stable simulation name from the description", () => {
    expect(deriveSimulationName("Simulate deploying a multi-stage pipeline with rollback!")).toBe(
      "simulate_deploying_multistage_pipeline",
    );
  });

  it("defaults non-simulation-like descriptions to the simulation family", () => {
    expect(inferSimulationFamily("Play a competitive game of tic tac toe")).toBe(
      "simulation",
    );
  });

  it("preserves simulation-like family detection for escalation scenarios", () => {
    expect(
      inferSimulationFamily(
        "Simulate when agents should escalate to a human operator versus acting autonomously",
      ),
    ).toBe("operator_loop");
  });

  it("builds an execution config with normalized run counts and optional sweep", () => {
    expect(
      buildSimulationExecutionConfig({
        description: "Simulate deployment",
        runs: 0,
        maxSteps: 7,
        sweep: [{ name: "budget", values: [50, 100], scale: "linear" }],
      }),
    ).toEqual({
      runs: 1,
      maxSteps: 7,
      sweep: [{ name: "budget", values: [50, 100], scale: "linear" }],
    });
  });

  it("resolves replay execution config from persisted execution metadata", () => {
    const report: SimulationResult = {
      id: "sim_1",
      name: "deploy_test",
      family: "simulation",
      status: "completed",
      description: "Deploy test",
      assumptions: [],
      variables: {},
      summary: { score: 0.8, reasoning: "ok", dimensionScores: { completion: 0.8 } },
      execution: {
        runs: 3,
        maxSteps: 12,
        sweep: [{ name: "max_steps", values: [1, 2], scale: "linear" }],
      },
      artifacts: { scenarioDir: "/tmp/deploy_test" },
      warnings: [],
    };

    expect(resolveSimulationExecutionConfig(report)).toEqual({
      runs: 3,
      maxSteps: 12,
      sweep: [{ name: "max_steps", values: [1, 2], scale: "linear" }],
    });
  });

  it("infers replay execution config from sweep reports when explicit execution metadata is missing", () => {
    const report: SimulationResult = {
      id: "sim_2",
      name: "sweep_test",
      family: "simulation",
      status: "completed",
      description: "Sweep test",
      assumptions: [],
      variables: {},
      summary: { score: 0.6, reasoning: "ok", dimensionScores: { completion: 0.6 } },
      sweep: {
        dimensions: [{ name: "threshold", values: [0.2, 0.4], scale: "linear" }],
        runs: 6,
        results: [
          {
            variables: { threshold: 0.2 },
            score: 0.4,
            reasoning: "first",
            dimensionScores: { completion: 0.4 },
          },
          {
            variables: { threshold: 0.4 },
            score: 0.8,
            reasoning: "second",
            dimensionScores: { completion: 0.8 },
          },
        ],
      },
      artifacts: { scenarioDir: "/tmp/sweep_test" },
      warnings: [],
    };

    expect(resolveSimulationExecutionConfig(report)).toEqual({
      runs: 3,
      sweep: [{ name: "threshold", values: [0.2, 0.4], scale: "linear" }],
    });
  });

  it("merges replay overrides on top of persisted variables", () => {
    const report: SimulationResult = {
      id: "sim_3",
      name: "override_test",
      family: "simulation",
      status: "completed",
      description: "Override test",
      assumptions: [],
      variables: { max_steps: 1, threshold: 0.4 },
      summary: { score: 0.3, reasoning: "ok", dimensionScores: { completion: 0.3 } },
      artifacts: { scenarioDir: "/tmp/override_test" },
      warnings: [],
    };

    expect(collectReplayVariables(report, { max_steps: 3, budget: 100 })).toEqual({
      max_steps: 3,
      threshold: 0.4,
      budget: 100,
    });
  });
});
