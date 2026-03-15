import { z } from "zod";
import { SimulationActionSpecSchema } from "./simulation-spec.js";

export const WorkflowStepSpecSchema = z.object({
  name: z.string().min(1),
  description: z.string().min(1),
  idempotent: z.boolean(),
  reversible: z.boolean(),
  compensation: z.string().min(1).nullable().optional(),
});

export const WorkflowSpecSchema = z.object({
  description: z.string().min(1),
  environmentDescription: z.string().min(1),
  initialStateDescription: z.string().min(1),
  workflowSteps: z.array(WorkflowStepSpecSchema).min(2),
  successCriteria: z.array(z.string()).min(2),
  actions: z.array(SimulationActionSpecSchema).min(2),
  failureModes: z.array(z.string()).default([]),
  maxSteps: z.number().int().positive().default(10),
});

export type WorkflowStepSpec = z.infer<typeof WorkflowStepSpecSchema>;
export type WorkflowSpec = z.infer<typeof WorkflowSpecSchema>;

export function parseRawWorkflowSpec(data: Record<string, unknown>): WorkflowSpec {
  return WorkflowSpecSchema.parse({
    description: data.description,
    environmentDescription: data.environment_description,
    initialStateDescription: data.initial_state_description,
    workflowSteps: data.workflow_steps,
    successCriteria: data.success_criteria,
    actions: data.actions,
    failureModes: data.failure_modes ?? [],
    maxSteps: data.max_steps ?? 10,
  });
}
