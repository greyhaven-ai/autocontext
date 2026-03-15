import { z } from "zod";
import { SimulationActionSpecSchema } from "./simulation-spec.js";

export const HiddenPreferencesSchema = z.object({
  priorities: z.record(z.number()),
  reservationValue: z.number(),
  aspirationValue: z.number(),
  batnaDescription: z.string().min(1),
});

export const NegotiationSpecSchema = z.object({
  description: z.string().min(1),
  environmentDescription: z.string().min(1),
  initialStateDescription: z.string().min(1),
  hiddenPreferences: HiddenPreferencesSchema,
  maxRounds: z.number().int().min(2).max(10),
  successCriteria: z.array(z.string().min(1)).min(1),
  failureModes: z.array(z.string().min(1)).default([]),
  actions: z.array(SimulationActionSpecSchema).min(2),
  maxSteps: z.number().int().nonnegative().default(0),
});

export type HiddenPreferences = z.infer<typeof HiddenPreferencesSchema>;
export type NegotiationSpec = z.infer<typeof NegotiationSpecSchema>;

export function parseRawNegotiationSpec(data: Record<string, unknown>): NegotiationSpec {
  const parsed = NegotiationSpecSchema.parse({
    description: data.description,
    environmentDescription: data.environment_description,
    initialStateDescription: data.initial_state_description,
    hiddenPreferences: (() => {
      const raw = data.hidden_preferences as Record<string, unknown>;
      return {
        priorities: raw.priorities,
        reservationValue: raw.reservation_value,
        aspirationValue: raw.aspiration_value,
        batnaDescription: raw.batna_description,
      };
    })(),
    maxRounds: data.max_rounds,
    successCriteria: data.success_criteria,
    failureModes: data.failure_modes ?? [],
    actions: data.actions,
    maxSteps: data.max_steps ?? 0,
  });
  return {
    ...parsed,
    maxSteps: parsed.maxSteps > 0 ? parsed.maxSteps : Math.max(parsed.maxRounds * 2, 4),
  };
}
