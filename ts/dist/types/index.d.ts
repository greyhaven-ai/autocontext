/**
 * Core types for MTS — mirrors Python dataclasses with Zod validation.
 */
import { z } from "zod";
export declare const CompletionResultSchema: z.ZodObject<{
    text: z.ZodString;
    model: z.ZodOptional<z.ZodNullable<z.ZodString>>;
    usage: z.ZodDefault<z.ZodRecord<z.ZodString, z.ZodNumber>>;
    costUsd: z.ZodOptional<z.ZodNullable<z.ZodNumber>>;
}, "strip", z.ZodTypeAny, {
    text: string;
    usage: Record<string, number>;
    model?: string | null | undefined;
    costUsd?: number | null | undefined;
}, {
    text: string;
    model?: string | null | undefined;
    usage?: Record<string, number> | undefined;
    costUsd?: number | null | undefined;
}>;
export type CompletionResult = z.infer<typeof CompletionResultSchema>;
export declare class ProviderError extends Error {
    constructor(message: string);
}
export interface LLMProvider {
    complete(opts: {
        systemPrompt: string;
        userPrompt: string;
        model?: string;
        temperature?: number;
        maxTokens?: number;
    }): Promise<CompletionResult>;
    defaultModel(): string;
    readonly name: string;
}
export declare const JudgeResultSchema: z.ZodObject<{
    score: z.ZodNumber;
    reasoning: z.ZodString;
    dimensionScores: z.ZodDefault<z.ZodRecord<z.ZodString, z.ZodNumber>>;
    rawResponses: z.ZodDefault<z.ZodArray<z.ZodString, "many">>;
}, "strip", z.ZodTypeAny, {
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
    rawResponses: string[];
}, {
    score: number;
    reasoning: string;
    dimensionScores?: Record<string, number> | undefined;
    rawResponses?: string[] | undefined;
}>;
export type JudgeResult = z.infer<typeof JudgeResultSchema>;
export declare const AgentTaskResultSchema: z.ZodObject<{
    score: z.ZodNumber;
    reasoning: z.ZodString;
    dimensionScores: z.ZodDefault<z.ZodRecord<z.ZodString, z.ZodNumber>>;
}, "strip", z.ZodTypeAny, {
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
}, {
    score: number;
    reasoning: string;
    dimensionScores?: Record<string, number> | undefined;
}>;
export type AgentTaskResult = z.infer<typeof AgentTaskResultSchema>;
export interface AgentTaskInterface {
    getTaskPrompt(state: Record<string, unknown>): string;
    evaluateOutput(output: string, state: Record<string, unknown>, opts?: {
        referenceContext?: string;
        requiredConcepts?: string[];
        calibrationExamples?: Array<Record<string, unknown>>;
    }): Promise<AgentTaskResult>;
    getRubric(): string;
    initialState(seed?: number): Record<string, unknown>;
    describeTask(): string;
    prepareContext?(state: Record<string, unknown>): Promise<Record<string, unknown>>;
    validateContext?(state: Record<string, unknown>): string[];
    reviseOutput?(output: string, judgeResult: AgentTaskResult, state: Record<string, unknown>): Promise<string>;
}
export declare const TaskStatusSchema: z.ZodEnum<["pending", "running", "completed", "failed"]>;
export type TaskStatus = z.infer<typeof TaskStatusSchema>;
export declare const TaskRowSchema: z.ZodObject<{
    id: z.ZodString;
    specName: z.ZodString;
    status: z.ZodEnum<["pending", "running", "completed", "failed"]>;
    priority: z.ZodDefault<z.ZodNumber>;
    configJson: z.ZodOptional<z.ZodNullable<z.ZodString>>;
    scheduledAt: z.ZodOptional<z.ZodNullable<z.ZodString>>;
    startedAt: z.ZodOptional<z.ZodNullable<z.ZodString>>;
    completedAt: z.ZodOptional<z.ZodNullable<z.ZodString>>;
    bestScore: z.ZodOptional<z.ZodNullable<z.ZodNumber>>;
    bestOutput: z.ZodOptional<z.ZodNullable<z.ZodString>>;
    totalRounds: z.ZodOptional<z.ZodNullable<z.ZodNumber>>;
    metThreshold: z.ZodDefault<z.ZodBoolean>;
    resultJson: z.ZodOptional<z.ZodNullable<z.ZodString>>;
    error: z.ZodOptional<z.ZodNullable<z.ZodString>>;
    createdAt: z.ZodString;
    updatedAt: z.ZodString;
}, "strip", z.ZodTypeAny, {
    status: "pending" | "running" | "completed" | "failed";
    id: string;
    specName: string;
    priority: number;
    metThreshold: boolean;
    createdAt: string;
    updatedAt: string;
    configJson?: string | null | undefined;
    scheduledAt?: string | null | undefined;
    startedAt?: string | null | undefined;
    completedAt?: string | null | undefined;
    bestScore?: number | null | undefined;
    bestOutput?: string | null | undefined;
    totalRounds?: number | null | undefined;
    resultJson?: string | null | undefined;
    error?: string | null | undefined;
}, {
    status: "pending" | "running" | "completed" | "failed";
    id: string;
    specName: string;
    createdAt: string;
    updatedAt: string;
    priority?: number | undefined;
    configJson?: string | null | undefined;
    scheduledAt?: string | null | undefined;
    startedAt?: string | null | undefined;
    completedAt?: string | null | undefined;
    bestScore?: number | null | undefined;
    bestOutput?: string | null | undefined;
    totalRounds?: number | null | undefined;
    metThreshold?: boolean | undefined;
    resultJson?: string | null | undefined;
    error?: string | null | undefined;
}>;
export type TaskRow = z.infer<typeof TaskRowSchema>;
export declare const RoundResultSchema: z.ZodObject<{
    roundNumber: z.ZodNumber;
    output: z.ZodString;
    score: z.ZodNumber;
    reasoning: z.ZodString;
    dimensionScores: z.ZodDefault<z.ZodRecord<z.ZodString, z.ZodNumber>>;
    isRevision: z.ZodDefault<z.ZodBoolean>;
    judgeFailed: z.ZodDefault<z.ZodBoolean>;
}, "strip", z.ZodTypeAny, {
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
    roundNumber: number;
    output: string;
    isRevision: boolean;
    judgeFailed: boolean;
}, {
    score: number;
    reasoning: string;
    roundNumber: number;
    output: string;
    dimensionScores?: Record<string, number> | undefined;
    isRevision?: boolean | undefined;
    judgeFailed?: boolean | undefined;
}>;
export type RoundResult = z.infer<typeof RoundResultSchema>;
export declare const ImprovementResultSchema: z.ZodObject<{
    rounds: z.ZodArray<z.ZodObject<{
        roundNumber: z.ZodNumber;
        output: z.ZodString;
        score: z.ZodNumber;
        reasoning: z.ZodString;
        dimensionScores: z.ZodDefault<z.ZodRecord<z.ZodString, z.ZodNumber>>;
        isRevision: z.ZodDefault<z.ZodBoolean>;
        judgeFailed: z.ZodDefault<z.ZodBoolean>;
    }, "strip", z.ZodTypeAny, {
        score: number;
        reasoning: string;
        dimensionScores: Record<string, number>;
        roundNumber: number;
        output: string;
        isRevision: boolean;
        judgeFailed: boolean;
    }, {
        score: number;
        reasoning: string;
        roundNumber: number;
        output: string;
        dimensionScores?: Record<string, number> | undefined;
        isRevision?: boolean | undefined;
        judgeFailed?: boolean | undefined;
    }>, "many">;
    bestOutput: z.ZodString;
    bestScore: z.ZodNumber;
    bestRound: z.ZodNumber;
    totalRounds: z.ZodNumber;
    metThreshold: z.ZodBoolean;
    judgeFailures: z.ZodDefault<z.ZodNumber>;
}, "strip", z.ZodTypeAny, {
    bestScore: number;
    bestOutput: string;
    totalRounds: number;
    metThreshold: boolean;
    rounds: {
        score: number;
        reasoning: string;
        dimensionScores: Record<string, number>;
        roundNumber: number;
        output: string;
        isRevision: boolean;
        judgeFailed: boolean;
    }[];
    bestRound: number;
    judgeFailures: number;
}, {
    bestScore: number;
    bestOutput: string;
    totalRounds: number;
    metThreshold: boolean;
    rounds: {
        score: number;
        reasoning: string;
        roundNumber: number;
        output: string;
        dimensionScores?: Record<string, number> | undefined;
        isRevision?: boolean | undefined;
        judgeFailed?: boolean | undefined;
    }[];
    bestRound: number;
    judgeFailures?: number | undefined;
}>;
export type ImprovementResult = z.infer<typeof ImprovementResultSchema>;
export declare const EventTypeSchema: z.ZodEnum<["threshold_met", "regression", "completion", "failure"]>;
export type EventType = z.infer<typeof EventTypeSchema>;
export declare const NotificationEventSchema: z.ZodObject<{
    eventType: z.ZodEnum<["threshold_met", "regression", "completion", "failure"]>;
    taskId: z.ZodString;
    specName: z.ZodString;
    score: z.ZodNumber;
    threshold: z.ZodOptional<z.ZodNumber>;
    round: z.ZodOptional<z.ZodNumber>;
    message: z.ZodString;
}, "strip", z.ZodTypeAny, {
    message: string;
    score: number;
    specName: string;
    eventType: "threshold_met" | "regression" | "completion" | "failure";
    taskId: string;
    threshold?: number | undefined;
    round?: number | undefined;
}, {
    message: string;
    score: number;
    specName: string;
    eventType: "threshold_met" | "regression" | "completion" | "failure";
    taskId: string;
    threshold?: number | undefined;
    round?: number | undefined;
}>;
export type NotificationEvent = z.infer<typeof NotificationEventSchema>;
//# sourceMappingURL=index.d.ts.map