import { z } from "zod";
import { SimulationActionSpecSchema } from "./simulation-spec.js";

export const EscalationPolicySchema = z.object({
  escalationThreshold: z.string().min(1),
  maxEscalations: z.number().int().positive(),
});

export const OperatorLoopSpecSchema = z.object({
  description: z.string().min(1),
  environmentDescription: z.string().min(1),
  initialStateDescription: z.string().min(1),
  escalationPolicy: EscalationPolicySchema,
  successCriteria: z.array(z.string().min(1)).min(1),
  failureModes: z.array(z.string().min(1)).default([]),
  actions: z.array(SimulationActionSpecSchema).min(2),
  maxSteps: z.number().int().positive().default(10),
});

export type EscalationPolicy = z.infer<typeof EscalationPolicySchema>;
export type OperatorLoopSpec = z.infer<typeof OperatorLoopSpecSchema>;

export function parseRawOperatorLoopSpec(data: Record<string, unknown>): OperatorLoopSpec {
  const rawPolicy = data.escalation_policy as Record<string, unknown>;
  return OperatorLoopSpecSchema.parse({
    description: data.description,
    environmentDescription: data.environment_description,
    initialStateDescription: data.initial_state_description,
    escalationPolicy: {
      escalationThreshold: rawPolicy.escalation_threshold,
      maxEscalations: rawPolicy.max_escalations,
    },
    successCriteria: data.success_criteria,
    failureModes: data.failure_modes ?? [],
    actions: data.actions,
    maxSteps: data.max_steps ?? 10,
  });
}
