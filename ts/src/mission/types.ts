/**
 * Mission type definitions (AC-410).
 *
 * Core data model for verifier-driven, long-running agent goals.
 */

import { z } from "zod";

export const MissionStatusSchema = z.enum([
  "active",
  "paused",
  "completed",
  "failed",
  "canceled",
]);

export type MissionStatus = z.infer<typeof MissionStatusSchema>;

export const MissionBudgetSchema = z.object({
  maxSteps: z.number().int().positive().optional(),
  maxCostUsd: z.number().positive().optional(),
  maxDurationMinutes: z.number().positive().optional(),
});

export type MissionBudget = z.infer<typeof MissionBudgetSchema>;

export const MissionSchema = z.object({
  id: z.string(),
  name: z.string(),
  goal: z.string(),
  status: MissionStatusSchema,
  budget: MissionBudgetSchema.optional(),
  metadata: z.record(z.unknown()).default({}),
  createdAt: z.string(),
  updatedAt: z.string().optional(),
  completedAt: z.string().optional(),
});

export type Mission = z.infer<typeof MissionSchema>;

export const StepStatusSchema = z.enum([
  "pending",
  "running",
  "completed",
  "failed",
  "skipped",
]);

export type StepStatus = z.infer<typeof StepStatusSchema>;

export const MissionStepSchema = z.object({
  id: z.string(),
  missionId: z.string(),
  description: z.string(),
  status: StepStatusSchema,
  result: z.string().optional(),
  createdAt: z.string(),
  completedAt: z.string().optional(),
});

export type MissionStep = z.infer<typeof MissionStepSchema>;

export const VerifierResultSchema = z.object({
  passed: z.boolean(),
  reason: z.string(),
  suggestions: z.array(z.string()).default([]),
  metadata: z.record(z.unknown()).default({}),
});

export type VerifierResult = z.infer<typeof VerifierResultSchema>;

export type MissionVerifier = (missionId: string) => Promise<VerifierResult>;

// ---------------------------------------------------------------------------
// MissionSpec — declarative mission definition (AC-411)
// ---------------------------------------------------------------------------

export const SubgoalSpecSchema = z.object({
  description: z.string(),
  priority: z.number().int().min(1).default(1),
});

export type SubgoalSpec = z.infer<typeof SubgoalSpecSchema>;

export const MissionSpecSchema = z.object({
  name: z.string(),
  goal: z.string(),
  verifierType: z.string().optional(),
  budget: MissionBudgetSchema.optional(),
  subgoals: z.array(SubgoalSpecSchema).optional(),
  metadata: z.record(z.unknown()).default({}),
});

export type MissionSpec = z.infer<typeof MissionSpecSchema>;

// ---------------------------------------------------------------------------
// Subgoal runtime type
// ---------------------------------------------------------------------------

export const SubgoalStatusSchema = z.enum(["pending", "active", "completed", "failed", "skipped"]);
export type SubgoalStatus = z.infer<typeof SubgoalStatusSchema>;

export const MissionSubgoalSchema = z.object({
  id: z.string(),
  missionId: z.string(),
  description: z.string(),
  priority: z.number().int(),
  status: SubgoalStatusSchema,
  createdAt: z.string(),
  completedAt: z.string().optional(),
});

export type MissionSubgoal = z.infer<typeof MissionSubgoalSchema>;

// ---------------------------------------------------------------------------
// Budget usage
// ---------------------------------------------------------------------------

export interface BudgetUsage {
  stepsUsed: number;
  maxSteps?: number;
  maxCostUsd?: number;
  exhausted: boolean;
}
