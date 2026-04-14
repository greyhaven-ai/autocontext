import type {
  SimulationStatus,
  SimulationSummary,
  SweepResult,
} from "./types.js";
import { normalizeSimulationScore } from "./score-normalization.js";

export interface SimulationRunResult {
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
}

export const DEGRADED_SCORE_THRESHOLD = 0.2;

export function deriveSimulationStatus(score: number): SimulationStatus {
  return score >= DEGRADED_SCORE_THRESHOLD ? "completed" : "degraded";
}

export function aggregateSimulationRuns(
  results: SimulationRunResult[],
): SimulationSummary {
  if (results.length === 0) {
    return { score: 0, reasoning: "No runs completed", dimensionScores: {} };
  }

  if (results.length === 1) {
    return results[0];
  }

  const avgScore =
    results.reduce((sum, result) => sum + result.score, 0) / results.length;
  const best = results.reduce((left, right) =>
    left.score > right.score ? left : right,
  );
  const worst = results.reduce((left, right) =>
    left.score < right.score ? left : right,
  );

  return {
    score: normalizeSimulationScore(avgScore),
    reasoning: `Average across ${results.length} runs`,
    dimensionScores: results[0].dimensionScores,
    bestCase: { score: best.score, variables: {} },
    worstCase: { score: worst.score, variables: {} },
  };
}

export function aggregateSimulationSweep(sweep: SweepResult): SimulationSummary {
  const results = sweep.results;
  if (results.length === 0) {
    return {
      score: 0,
      reasoning: "No sweep runs completed",
      dimensionScores: {},
    };
  }

  const avgScore =
    results.reduce((sum, result) => sum + result.score, 0) / results.length;
  const best = results.reduce((left, right) =>
    left.score > right.score ? left : right,
  );
  const worst = results.reduce((left, right) =>
    left.score < right.score ? left : right,
  );

  const sensitivity: Array<{ name: string; variance: number }> = [];
  for (const dimension of sweep.dimensions) {
    const scoresByValue = new Map<number, number[]>();
    for (const result of results) {
      const value = result.variables[dimension.name] as number;
      if (value != null) {
        const scores = scoresByValue.get(value) ?? [];
        scores.push(result.score);
        scoresByValue.set(value, scores);
      }
    }
    const means = [...scoresByValue.values()].map(
      (scores) => scores.reduce((sum, score) => sum + score, 0) / scores.length,
    );
    if (means.length > 1) {
      const range = Math.max(...means) - Math.min(...means);
      sensitivity.push({ name: dimension.name, variance: range });
    }
  }
  sensitivity.sort((left, right) => right.variance - left.variance);

  return {
    score: normalizeSimulationScore(avgScore),
    reasoning: `Sweep across ${sweep.dimensions.length} dimension(s), ${results.length} runs`,
    dimensionScores: results[0].dimensionScores,
    bestCase: { score: best.score, variables: best.variables },
    worstCase: { score: worst.score, variables: worst.variables },
    mostSensitiveVariables: sensitivity.map((entry) => entry.name),
  };
}

export function buildSimulationAssumptions(
  spec: Record<string, unknown>,
  family: string,
  variables?: Record<string, unknown>,
): string[] {
  const assumptions: string[] = [];
  assumptions.push(
    `Modeled as a ${family} scenario with ${(spec.actions as unknown[])?.length ?? 0} actions`,
  );
  if (spec.max_steps || spec.maxSteps) {
    assumptions.push(`Bounded to ${spec.max_steps ?? spec.maxSteps} maximum steps`);
  }
  if (spec.success_criteria || spec.successCriteria) {
    const criteria = (spec.success_criteria ?? spec.successCriteria) as string[];
    assumptions.push(`Success defined as: ${criteria.join(", ")}`);
  }
  if (variables && Object.keys(variables).length > 0) {
    assumptions.push(`Requested parameters: ${JSON.stringify(variables)}`);
  }
  if (family === "operator_loop") {
    assumptions.push(
      "Runtime includes at least one clarification request and an operator review checkpoint.",
    );
  }
  if (family === "coordination") {
    assumptions.push(
      "Runtime records worker handoffs and merges outputs during execution.",
    );
  }
  assumptions.push("Agent selects actions greedily (first available)");
  assumptions.push(
    "Environment is deterministic given the same seed and parameter set",
  );
  return assumptions;
}

export function buildSimulationWarnings(
  family: string,
  providerName: string,
): string[] {
  const warnings = [
    "Model-driven result only; not empirical evidence.",
    `Simulated using the ${family} family with generated action logic.`,
    "Outcomes depend on the quality of the LLM-generated scenario spec.",
    "Variable sensitivity analysis is based on score variance across sweep values, not causal attribution.",
  ];
  if (providerName === "deterministic") {
    warnings.push(
      "Synthetic deterministic provider in use; results are placeholder and not model-derived.",
    );
  }
  return warnings;
}
