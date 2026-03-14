import { z } from "zod";

export const SimulationActionSpecSchema = z.object({
  name: z.string().min(1),
  description: z.string().min(1),
  parameters: z.record(z.string()).default({}),
  preconditions: z.array(z.string()).default([]),
  effects: z.array(z.string()).default([]),
});

export const SimulationSpecSchema = z.object({
  description: z.string().min(1),
  environmentDescription: z.string().min(1),
  initialStateDescription: z.string().min(1),
  successCriteria: z.array(z.string()).min(2),
  failureModes: z.array(z.string()).default([]),
  actions: z.array(SimulationActionSpecSchema).min(2),
  maxSteps: z.number().int().positive().default(10),
});

export type SimulationActionSpec = z.infer<typeof SimulationActionSpecSchema>;
export type SimulationSpec = z.infer<typeof SimulationSpecSchema>;

export function parseRawSimulationSpec(data: Record<string, unknown>): SimulationSpec {
  return SimulationSpecSchema.parse({
    description: data.description,
    environmentDescription: data.environment_description,
    initialStateDescription: data.initial_state_description,
    successCriteria: data.success_criteria,
    failureModes: data.failure_modes ?? [],
    actions: data.actions,
    maxSteps: data.max_steps ?? 10,
  });
}
