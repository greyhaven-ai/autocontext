import { createHash } from "node:crypto";
import { z } from "zod";

/** Shared wire contract. Runtime-specific prompt/transformation versions must be explicit. */
export const JudgeServingSpecSchema = z.object({
  schema_version: z.literal("autocontext.judge-serving.v1"),
  compiled_rubric: z.string(),
  judge_provider: z.string(),
  judge_model: z.string(),
  prompt_template_version: z.string(),
  serving_examples: z.array(z.string()).readonly(),
  pinned_dimensions: z.array(z.string()).readonly(),
  score_transformations: z.array(z.string()).readonly(),
  extension_fingerprint: z.string().nullable(),
  evaluation_context_hash: z.string().nullable(),
}).strict().readonly();

export type JudgeServingSpec = z.infer<typeof JudgeServingSpecSchema>;

export function canonicalJudgeSpec(input: JudgeServingSpec): string {
  const spec = JudgeServingSpecSchema.parse(input);
  return JSON.stringify(Object.fromEntries(Object.entries(spec).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)));
}

export function judgeSpecEpoch(input: JudgeServingSpec): string {
  return createHash("sha256").update(canonicalJudgeSpec(input), "utf8").digest("hex");
}
