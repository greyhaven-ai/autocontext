import { z } from "zod";
import { SimulationActionSpecSchema } from "./simulation-spec.js";

export const SchemaEvolutionMutationSchema = z.object({
  version: z.number().int().positive(),
  description: z.string().min(1),
  breaking: z.boolean(),
  fieldsAdded: z.array(z.string().min(1)).default([]),
  fieldsRemoved: z.array(z.string().min(1)).default([]),
  fieldsModified: z.record(z.string()).default({}),
});

export const SchemaEvolutionSpecSchema = z.object({
  description: z.string().min(1),
  environmentDescription: z.string().min(1),
  initialStateDescription: z.string().min(1),
  mutations: z.array(SchemaEvolutionMutationSchema).min(2),
  successCriteria: z.array(z.string().min(1)).min(1),
  failureModes: z.array(z.string().min(1)).default([]),
  actions: z.array(SimulationActionSpecSchema).min(2),
  maxSteps: z.number().int().positive().default(10),
});

export type SchemaEvolutionMutation = z.infer<typeof SchemaEvolutionMutationSchema>;
export type SchemaEvolutionSpec = z.infer<typeof SchemaEvolutionSpecSchema>;

export function parseRawSchemaEvolutionSpec(data: Record<string, unknown>): SchemaEvolutionSpec {
  return SchemaEvolutionSpecSchema.parse({
    description: data.description,
    environmentDescription: data.environment_description,
    initialStateDescription: data.initial_state_description,
    mutations: Array.isArray(data.mutations)
      ? data.mutations.map((mutation) => {
          const raw = mutation as Record<string, unknown>;
          return {
            version: raw.version,
            description: raw.description,
            breaking: raw.breaking,
            fieldsAdded: raw.fields_added ?? [],
            fieldsRemoved: raw.fields_removed ?? [],
            fieldsModified: raw.fields_modified ?? {},
          };
        })
      : data.mutations,
    successCriteria: data.success_criteria,
    failureModes: data.failure_modes ?? [],
    actions: data.actions,
    maxSteps: data.max_steps ?? 10,
  });
}
