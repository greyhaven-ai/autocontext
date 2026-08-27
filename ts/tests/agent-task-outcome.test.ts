import { describe, expect, it } from "vitest";

import {
  AgentTaskOutcomeV1Schema,
  agentTaskOutcomeReceiptV1,
  buildAgentTaskOutcomeV1,
} from "../src/knowledge/agent-task-outcome.js";
import { sanitizeRunTranscriptMessage } from "../src/server/run-transcript-frame.js";
import type { ImprovementResult } from "../src/types/index.js";

const improvementResult: ImprovementResult = {
  rounds: [
    {
      roundNumber: 1,
      output: "Initial",
      score: 0.61,
      reasoning: "The evidence is accurate but the recommendation is vague.",
      dimensionScores: { grounding: 0.9, actionability: 0.32 },
      isRevision: false,
      judgeFailed: false,
      evaluatorEpoch: "judge-v1",
    },
    {
      roundNumber: 2,
      output: "Revised",
      score: 0.93,
      reasoning: "The evidence is preserved and the recommendation is actionable.",
      dimensionScores: { grounding: 0.94, actionability: 0.92 },
      isRevision: true,
      judgeFailed: false,
      evaluatorEpoch: "judge-v1",
    },
  ],
  bestOutput: "Revised",
  bestScore: 0.93,
  bestRound: 2,
  totalRounds: 2,
  metThreshold: true,
  judgeFailures: 0,
  terminationReason: "threshold_met",
  dimensionTrajectory: { grounding: [0.9, 0.94], actionability: [0.32, 0.92] },
  totalInternalRetries: 0,
  judgeCalls: 2,
  evaluatorEpoch: "judge-v1",
};

describe("agent_task_outcome_v1", () => {
  it("derives mechanics and evaluator evidence directly from the improvement result", () => {
    const outcome = buildAgentTaskOutcomeV1({
      result: improvementResult,
      qualityThreshold: 0.9,
      maxIterations: 4,
    });

    expect(outcome).toEqual({
      schema_version: 1,
      termination_reason: "threshold_met",
      quality_threshold: 0.9,
      met_threshold: true,
      completed_iterations: 2,
      max_iterations: 4,
      best_iteration: 2,
      best_score: 0.93,
      generations: [
        {
          generation: 1,
          score: 0.61,
          reasoning: "The evidence is accurate but the recommendation is vague.",
          dimension_scores: { grounding: 0.9, actionability: 0.32 },
          judge_failed: false,
          evaluator_epoch: "judge-v1",
        },
        {
          generation: 2,
          score: 0.93,
          reasoning: "The evidence is preserved and the recommendation is actionable.",
          dimension_scores: { grounding: 0.94, actionability: 0.92 },
          judge_failed: false,
          evaluator_epoch: "judge-v1",
        },
      ],
    });
    expect(agentTaskOutcomeReceiptV1(outcome)).not.toHaveProperty("generations");
  });

  it("rejects incomplete or internally inconsistent outcomes", () => {
    const outcome = buildAgentTaskOutcomeV1({
      result: improvementResult,
      qualityThreshold: 0.9,
      maxIterations: 4,
    });

    expect(() =>
      AgentTaskOutcomeV1Schema.parse({
        ...outcome,
        completed_iterations: 3,
      }),
    ).toThrow(/exactly one evaluation/);
    expect(() =>
      AgentTaskOutcomeV1Schema.parse({
        ...outcome,
        best_score: 0.5,
      }),
    ).toThrow(/score of best_iteration/);
    expect(() =>
      AgentTaskOutcomeV1Schema.parse({
        ...outcome,
        met_threshold: false,
      }),
    ).toThrow(/exactly when termination_reason is threshold_met/);
    expect(() =>
      AgentTaskOutcomeV1Schema.parse({
        ...outcome,
        termination_reason: "max_rounds",
      }),
    ).toThrow(/exactly when termination_reason is threshold_met/);
  });

  it("retains the terminal receipt and per-generation evaluator evidence", () => {
    const outcome = buildAgentTaskOutcomeV1({
      result: improvementResult,
      qualityThreshold: 0.9,
      maxIterations: 4,
    });
    const generation = sanitizeRunTranscriptMessage({
      type: "event",
      event: "generation_completed",
      payload: {
        run_id: "run-1",
        generation: 2,
        mean_score: 0.93,
        best_score: 0.93,
        family: "agent_task",
        rounds_completed: 2,
        reasoning: outcome.generations[1]?.reasoning,
        dimension_scores: outcome.generations[1]?.dimension_scores,
        judge_failed: false,
        evaluator_epoch: "judge-v1",
      },
    });
    const completed = sanitizeRunTranscriptMessage({
      type: "event",
      event: "run_completed",
      payload: {
        run_id: "run-1",
        completed_generations: 2,
        best_score: 0.93,
        family: "agent_task",
        agent_task_outcome: agentTaskOutcomeReceiptV1(outcome),
      },
    });

    expect(generation).toMatchObject({
      type: "event",
      payload: {
        reasoning: "The evidence is preserved and the recommendation is actionable.",
        dimension_scores: { grounding: 0.94, actionability: 0.92 },
        judge_failed: false,
        evaluator_epoch: "judge-v1",
      },
    });
    expect(completed).toMatchObject({
      type: "event",
      payload: {
        agent_task_outcome: {
          schema_version: 1,
          termination_reason: "threshold_met",
          completed_iterations: 2,
          best_iteration: 2,
        },
      },
    });
  });
});
