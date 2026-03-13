/**
 * AgentTaskSpec — specification for an agent task scenario.
 * Port of autocontext/src/autocontext/scenarios/custom/agent_task_spec.py
 */

import { z } from "zod";

export const AgentTaskSpecSchema = z.object({
  taskPrompt: z.string().min(1, "task_prompt must not be empty"),
  judgeRubric: z.string().min(1, "judge_rubric must not be empty"),
  outputFormat: z.enum(["free_text", "json_schema", "code"]).default("free_text"),
  judgeModel: z.string().default(""),
  difficultyTiers: z.array(z.record(z.unknown())).nullable().optional(),
  referenceContext: z.string().min(1, "reference_context, if provided, must not be empty").nullable().optional(),
  referenceSources: z.array(z.string().min(1)).min(1, "reference_sources, if provided, must not be empty").nullable().optional(),
  requiredConcepts: z.array(z.string().min(1)).min(1, "required_concepts, if provided, must not be empty").nullable().optional(),
  calibrationExamples: z.array(z.record(z.unknown())).nullable().optional(),
  contextPreparation: z.string().min(1, "context_preparation, if provided, must not be empty").nullable().optional(),
  requiredContextKeys: z.array(z.string().min(1)).min(1, "required_context_keys, if provided, must not be empty").nullable().optional(),
  maxRounds: z.number().int().min(1, "max_rounds must be >= 1").default(1),
  qualityThreshold: z.number().gt(0).lte(1, "quality_threshold must be between 0.0 (exclusive) and 1.0 (inclusive)").default(0.9),
  revisionPrompt: z.string().min(1, "revision_prompt, if provided, must not be empty").nullable().optional(),
  sampleInput: z.string().min(1, "sample_input, if provided, must not be empty").nullable().optional(),
});

export type AgentTaskSpec = z.infer<typeof AgentTaskSpecSchema>;

/**
 * Parse a raw JSON object (snake_case from LLM) into an AgentTaskSpec.
 */
export function parseRawSpec(data: Record<string, unknown>): AgentTaskSpec {
  return AgentTaskSpecSchema.parse({
    taskPrompt: data.task_prompt,
    judgeRubric: data.judge_rubric,
    outputFormat: data.output_format ?? "free_text",
    judgeModel: data.judge_model ?? "",
    difficultyTiers: data.difficulty_tiers ?? null,
    referenceContext: data.reference_context ?? null,
    referenceSources: data.reference_sources ?? null,
    requiredConcepts: data.required_concepts ?? null,
    calibrationExamples: data.calibration_examples ?? null,
    contextPreparation: data.context_preparation ?? null,
    requiredContextKeys: data.required_context_keys ?? null,
    maxRounds: data.max_rounds ?? 1,
    qualityThreshold: data.quality_threshold ?? 0.9,
    revisionPrompt: data.revision_prompt ?? null,
    sampleInput: data.sample_input ?? null,
  });
}
