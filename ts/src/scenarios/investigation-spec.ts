import { z } from "zod";
import { SimulationActionSpecSchema } from "./simulation-spec.js";

export const InvestigationSpecSchema = z.object({
  description: z.string().min(1),
  environmentDescription: z.string().min(1),
  initialStateDescription: z.string().min(1),
  evidencePoolDescription: z.string().min(1),
  diagnosisTarget: z.string().min(1),
  successCriteria: z.array(z.string()).min(2),
  failureModes: z.array(z.string()).default([]),
  actions: z.array(SimulationActionSpecSchema).min(2),
  maxSteps: z.number().int().positive().default(10),
});

export type InvestigationSpec = z.infer<typeof InvestigationSpecSchema>;

export function parseRawInvestigationSpec(data: Record<string, unknown>): InvestigationSpec {
  return InvestigationSpecSchema.parse({
    description: data.description,
    environmentDescription: data.environment_description,
    initialStateDescription: data.initial_state_description,
    evidencePoolDescription: data.evidence_pool_description,
    diagnosisTarget: data.diagnosis_target,
    successCriteria: data.success_criteria,
    failureModes: data.failure_modes ?? [],
    actions: data.actions,
    maxSteps: data.max_steps ?? 10,
  });
}
