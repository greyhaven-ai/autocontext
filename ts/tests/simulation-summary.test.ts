import { describe, expect, it } from "vitest";
import {
  aggregateSimulationRuns,
  aggregateSimulationSweep,
  buildSimulationAssumptions,
  buildSimulationWarnings,
} from "../src/simulation/summary.js";

describe("simulation summary", () => {
  it("aggregates multiple run results into an averaged summary", () => {
    expect(
      aggregateSimulationRuns([
        {
          score: 0.2,
          reasoning: "first",
          dimensionScores: { completion: 0.2 },
        },
        {
          score: 0.6,
          reasoning: "second",
          dimensionScores: { completion: 0.6 },
        },
      ]),
    ).toEqual({
      score: 0.4,
      reasoning: "Average across 2 runs",
      dimensionScores: { completion: 0.2 },
      bestCase: { score: 0.6, variables: {} },
      worstCase: { score: 0.2, variables: {} },
    });
  });

  it("aggregates sweep runs and reports sensitivity ordering", () => {
    expect(
      aggregateSimulationSweep({
        dimensions: [
          { name: "max_steps", values: [1, 2], scale: "linear" },
          { name: "mode", values: ["safe", "fast"], scale: "categorical" },
        ],
        runs: 4,
        results: [
          {
            variables: { max_steps: 1, mode: "safe" },
            score: 0.2,
            reasoning: "a",
            dimensionScores: { completion: 0.2 },
          },
          {
            variables: { max_steps: 2, mode: "safe" },
            score: 0.8,
            reasoning: "b",
            dimensionScores: { completion: 0.8 },
          },
          {
            variables: { max_steps: 1, mode: "fast" },
            score: 0.3,
            reasoning: "c",
            dimensionScores: { completion: 0.3 },
          },
          {
            variables: { max_steps: 2, mode: "fast" },
            score: 0.7,
            reasoning: "d",
            dimensionScores: { completion: 0.7 },
          },
        ],
      }),
    ).toEqual({
      score: 0.5,
      reasoning: "Sweep across 2 dimension(s), 4 runs",
      dimensionScores: { completion: 0.2 },
      bestCase: { score: 0.8, variables: { max_steps: 2, mode: "safe" } },
      worstCase: { score: 0.2, variables: { max_steps: 1, mode: "safe" } },
      mostSensitiveVariables: ["max_steps", "mode"],
    });
  });

  it("builds assumptions that reflect variables and family-specific runtime behavior", () => {
    expect(
      buildSimulationAssumptions(
        {
          actions: [{ name: "step_a" }, { name: "step_b" }],
          max_steps: 3,
          success_criteria: ["resolve incident", "avoid regression"],
        },
        "operator_loop",
        { threshold: 0.7 },
      ),
    ).toEqual([
      "Modeled as a operator_loop scenario with 2 actions",
      "Bounded to 3 maximum steps",
      "Success defined as: resolve incident, avoid regression",
      'Requested parameters: {"threshold":0.7}',
      "Runtime includes at least one clarification request and an operator review checkpoint.",
      "Agent selects actions greedily (first available)",
      "Environment is deterministic given the same seed and parameter set",
    ]);
  });

  it("adds deterministic-provider warnings when the provider is synthetic", () => {
    expect(buildSimulationWarnings("simulation", "deterministic")).toContain(
      "Synthetic deterministic provider in use; results are placeholder and not model-derived.",
    );
  });
});
