import { z } from "zod";
import { SimulationActionSpecSchema } from "./simulation-spec.js";

export const WorkerSpecSchema = z.object({
  workerId: z.string().min(1),
  role: z.string().min(1),
});

export const CoordinationSpecSchema = z.object({
  description: z.string().min(1),
  environmentDescription: z.string().min(1),
  initialStateDescription: z.string().min(1),
  workers: z.array(WorkerSpecSchema).min(2),
  successCriteria: z.array(z.string().min(1)).min(1),
  failureModes: z.array(z.string().min(1)).default([]),
  actions: z.array(SimulationActionSpecSchema).min(2),
  maxSteps: z.number().int().positive().default(10),
});

export type WorkerSpec = z.infer<typeof WorkerSpecSchema>;
export type CoordinationSpec = z.infer<typeof CoordinationSpecSchema>;

export function parseRawCoordinationSpec(data: Record<string, unknown>): CoordinationSpec {
  return CoordinationSpecSchema.parse({
    description: data.description,
    environmentDescription: data.environment_description,
    initialStateDescription: data.initial_state_description,
    workers: Array.isArray(data.workers)
      ? data.workers.map((worker) => {
          const raw = worker as Record<string, unknown>;
          return {
            workerId: raw.worker_id,
            role: raw.role,
          };
        })
      : data.workers,
    successCriteria: data.success_criteria,
    failureModes: data.failure_modes ?? [],
    actions: data.actions,
    maxSteps: data.max_steps ?? 10,
  });
}
