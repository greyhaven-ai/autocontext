import { z } from "zod";

export const ArtifactSpecSchema = z.object({
  path: z.string().min(1),
  content: z.string(),
  contentType: z.string().min(1),
  metadata: z.record(z.unknown()).default({}),
});

export const ArtifactEditingSpecSchema = z.object({
  taskDescription: z.string().min(1),
  rubric: z.string().min(1),
  validationRules: z.array(z.string().min(1)).min(1),
  artifacts: z.array(ArtifactSpecSchema).min(1),
});

export type ArtifactSpec = z.infer<typeof ArtifactSpecSchema>;
export type ArtifactEditingSpec = z.infer<typeof ArtifactEditingSpecSchema>;

export function parseRawArtifactEditingSpec(data: Record<string, unknown>): ArtifactEditingSpec {
  return ArtifactEditingSpecSchema.parse({
    taskDescription: data.task_description,
    rubric: data.rubric,
    validationRules: data.validation_rules,
    artifacts: Array.isArray(data.artifacts)
      ? data.artifacts.map((artifact) => {
          const raw = artifact as Record<string, unknown>;
          return {
            path: raw.path,
            content: raw.content,
            contentType: raw.content_type,
            metadata: raw.metadata ?? {},
          };
        })
      : data.artifacts,
  });
}
