/**
 * Core types for MTS — mirrors Python dataclasses with Zod validation.
 */
import { z } from "zod";
// ---------------------------------------------------------------------------
// Completion / Provider types
// ---------------------------------------------------------------------------
export const CompletionResultSchema = z.object({
    text: z.string(),
    model: z.string().nullish(),
    usage: z.record(z.number()).default({}),
    costUsd: z.number().nullish(),
});
export class ProviderError extends Error {
    constructor(message) {
        super(message);
        this.name = "ProviderError";
    }
}
// ---------------------------------------------------------------------------
// Judge types
// ---------------------------------------------------------------------------
export const JudgeResultSchema = z.object({
    score: z.number().min(0).max(1),
    reasoning: z.string(),
    dimensionScores: z.record(z.number().min(0).max(1)).default({}),
    rawResponses: z.array(z.string()).default([]),
});
// ---------------------------------------------------------------------------
// Agent task types
// ---------------------------------------------------------------------------
export const AgentTaskResultSchema = z.object({
    score: z.number().min(0).max(1),
    reasoning: z.string(),
    dimensionScores: z.record(z.number().min(0).max(1)).default({}),
});
// ---------------------------------------------------------------------------
// Task queue types
// ---------------------------------------------------------------------------
export const TaskStatusSchema = z.enum(["pending", "running", "completed", "failed"]);
export const TaskRowSchema = z.object({
    id: z.string(),
    specName: z.string(),
    status: TaskStatusSchema,
    priority: z.number().int().default(0),
    configJson: z.string().nullish(),
    scheduledAt: z.string().nullish(),
    startedAt: z.string().nullish(),
    completedAt: z.string().nullish(),
    bestScore: z.number().nullish(),
    bestOutput: z.string().nullish(),
    totalRounds: z.number().int().nullish(),
    metThreshold: z.boolean().default(false),
    resultJson: z.string().nullish(),
    error: z.string().nullish(),
    createdAt: z.string(),
    updatedAt: z.string(),
});
// ---------------------------------------------------------------------------
// Improvement loop types
// ---------------------------------------------------------------------------
export const RoundResultSchema = z.object({
    roundNumber: z.number().int(),
    output: z.string(),
    score: z.number(),
    reasoning: z.string(),
    dimensionScores: z.record(z.number()).default({}),
    isRevision: z.boolean().default(false),
    judgeFailed: z.boolean().default(false),
});
export const ImprovementResultSchema = z.object({
    rounds: z.array(RoundResultSchema),
    bestOutput: z.string(),
    bestScore: z.number(),
    bestRound: z.number().int(),
    totalRounds: z.number().int(),
    metThreshold: z.boolean(),
    judgeFailures: z.number().int().default(0),
});
// ---------------------------------------------------------------------------
// Notification types
// ---------------------------------------------------------------------------
export const EventTypeSchema = z.enum([
    "threshold_met",
    "regression",
    "completion",
    "failure",
]);
export const NotificationEventSchema = z.object({
    eventType: EventTypeSchema,
    taskId: z.string(),
    specName: z.string(),
    score: z.number(),
    threshold: z.number().optional(),
    round: z.number().int().optional(),
    message: z.string(),
});
//# sourceMappingURL=index.js.map