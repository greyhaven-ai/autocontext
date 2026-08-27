import { z } from "zod";

import type { ImprovementResult } from "../types/index.js";

/** Durable, synthesis-free contract for a completed structured agent-task loop. */
export const AGENT_TASK_OUTCOME_CAPABILITY = "agent_task_outcome_v1";
export const AGENT_TASK_OUTCOME_SCHEMA_VERSION = 1 as const;

export const AgentTaskTerminationReasonSchema = z.enum([
  "threshold_met",
  "max_rounds",
  "plateau_stall",
  "unchanged_output",
  "consecutive_failures",
]);

export const AgentTaskOutcomeGenerationV1Schema = z
  .object({
    generation: z.number().int().positive(),
    score: z.number().finite(),
    reasoning: z.string(),
    dimension_scores: z.record(z.number().finite()),
    judge_failed: z.boolean(),
    evaluator_epoch: z.string().nullable(),
  })
  .strict();

const receiptShape = {
  schema_version: z.literal(AGENT_TASK_OUTCOME_SCHEMA_VERSION),
  termination_reason: AgentTaskTerminationReasonSchema,
  quality_threshold: z.number().gt(0).lte(1),
  met_threshold: z.boolean(),
  completed_iterations: z.number().int().positive(),
  max_iterations: z.number().int().positive(),
  best_iteration: z.number().int().positive(),
  best_score: z.number().finite(),
} as const;

function validateIterationBounds(
  value: {
    completed_iterations: number;
    max_iterations: number;
    best_iteration: number;
  },
  ctx: z.RefinementCtx,
): void {
  if (value.completed_iterations > value.max_iterations) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ["completed_iterations"],
      message: "completed_iterations must not exceed max_iterations",
    });
  }
  if (value.best_iteration > value.completed_iterations) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ["best_iteration"],
      message: "best_iteration must not exceed completed_iterations",
    });
  }
}

function validateTerminationSemantics(
  value: {
    termination_reason: AgentTaskTerminationReason;
    met_threshold: boolean;
  },
  ctx: z.RefinementCtx,
): void {
  if ((value.termination_reason === "threshold_met") !== value.met_threshold) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ["met_threshold"],
      message: "met_threshold must be true exactly when termination_reason is threshold_met",
    });
  }
}

export const AgentTaskOutcomeReceiptV1Schema = z
  .object(receiptShape)
  .strict()
  .superRefine((value, ctx) => {
    validateIterationBounds(value, ctx);
    validateTerminationSemantics(value, ctx);
  });

export const AgentTaskOutcomeV1Schema = z
  .object({
    ...receiptShape,
    generations: z.array(AgentTaskOutcomeGenerationV1Schema).min(1),
  })
  .strict()
  .superRefine((value, ctx) => {
    validateIterationBounds(value, ctx);
    validateTerminationSemantics(value, ctx);
    if (value.generations.length !== value.completed_iterations) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["generations"],
        message: "generations must contain exactly one evaluation per completed iteration",
      });
    }
    for (let index = 0; index < value.generations.length; index += 1) {
      const expectedGeneration = index + 1;
      if (value.generations[index]?.generation !== expectedGeneration) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["generations", index, "generation"],
          message: `generation must be ${expectedGeneration}`,
        });
      }
    }
    const bestGeneration = value.generations[value.best_iteration - 1];
    if (bestGeneration && bestGeneration.score !== value.best_score) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["best_score"],
        message: "best_score must equal the score of best_iteration",
      });
    }
  });

export type AgentTaskTerminationReason = z.infer<typeof AgentTaskTerminationReasonSchema>;
export type AgentTaskOutcomeGenerationV1 = z.infer<typeof AgentTaskOutcomeGenerationV1Schema>;
export type AgentTaskOutcomeReceiptV1 = z.infer<typeof AgentTaskOutcomeReceiptV1Schema>;
export type AgentTaskOutcomeV1 = z.infer<typeof AgentTaskOutcomeV1Schema>;

export function buildAgentTaskOutcomeV1(opts: {
  result: ImprovementResult;
  qualityThreshold: number;
  maxIterations: number;
}): AgentTaskOutcomeV1 {
  return AgentTaskOutcomeV1Schema.parse({
    schema_version: AGENT_TASK_OUTCOME_SCHEMA_VERSION,
    termination_reason: opts.result.terminationReason,
    quality_threshold: opts.qualityThreshold,
    met_threshold: opts.result.metThreshold,
    completed_iterations: opts.result.totalRounds,
    max_iterations: opts.maxIterations,
    best_iteration: opts.result.bestRound,
    best_score: opts.result.bestScore,
    generations: opts.result.rounds.map((round) => ({
      generation: round.roundNumber,
      score: round.score,
      reasoning: round.reasoning,
      dimension_scores: { ...round.dimensionScores },
      judge_failed: round.judgeFailed,
      evaluator_epoch: round.evaluatorEpoch ?? null,
    })),
  });
}

export function agentTaskOutcomeReceiptV1(outcome: AgentTaskOutcomeV1): AgentTaskOutcomeReceiptV1 {
  return AgentTaskOutcomeReceiptV1Schema.parse({
    schema_version: outcome.schema_version,
    termination_reason: outcome.termination_reason,
    quality_threshold: outcome.quality_threshold,
    met_threshold: outcome.met_threshold,
    completed_iterations: outcome.completed_iterations,
    max_iterations: outcome.max_iterations,
    best_iteration: outcome.best_iteration,
    best_score: outcome.best_score,
  });
}
