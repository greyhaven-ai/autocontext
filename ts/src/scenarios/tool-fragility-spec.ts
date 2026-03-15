import { z } from "zod";
import { SimulationActionSpecSchema } from "./simulation-spec.js";

export const ToolContractSpecSchema = z.object({
  toolName: z.string().min(1),
  version: z.number().int().positive(),
  description: z.string().min(1),
});

export const ToolFragilitySpecSchema = z.object({
  description: z.string().min(1),
  environmentDescription: z.string().min(1),
  initialStateDescription: z.string().min(1),
  toolContracts: z.array(ToolContractSpecSchema).min(2),
  successCriteria: z.array(z.string().min(1)).min(1),
  failureModes: z.array(z.string().min(1)).default([]),
  actions: z.array(SimulationActionSpecSchema).min(2),
  maxSteps: z.number().int().positive().default(10),
});

export type ToolContractSpec = z.infer<typeof ToolContractSpecSchema>;
export type ToolFragilitySpec = z.infer<typeof ToolFragilitySpecSchema>;

export function parseRawToolFragilitySpec(data: Record<string, unknown>): ToolFragilitySpec {
  return ToolFragilitySpecSchema.parse({
    description: data.description,
    environmentDescription: data.environment_description,
    initialStateDescription: data.initial_state_description,
    toolContracts: Array.isArray(data.tool_contracts)
      ? data.tool_contracts.map((toolContract) => {
          const raw = toolContract as Record<string, unknown>;
          return {
            toolName: raw.tool_name,
            version: raw.version,
            description: raw.description,
          };
        })
      : data.tool_contracts,
    successCriteria: data.success_criteria,
    failureModes: data.failure_modes ?? [],
    actions: data.actions,
    maxSteps: data.max_steps ?? 10,
  });
}
