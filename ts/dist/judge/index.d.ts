/**
 * LLM-based judge for evaluating agent task outputs.
 * Port of mts/src/mts/execution/judge.py
 */
import type { LLMProvider, JudgeResult } from "../types/index.js";
export { parseJudgeResponse } from "./parse.js";
export type { ParsedJudge } from "./parse.js";
export interface LLMJudgeOpts {
    provider: LLMProvider;
    model: string;
    rubric: string;
    samples?: number;
    temperature?: number;
}
export declare class LLMJudge {
    private provider;
    readonly model: string;
    readonly rubric: string;
    private samples;
    private temperature;
    constructor(opts: LLMJudgeOpts);
    evaluate(opts: {
        taskPrompt: string;
        agentOutput: string;
        referenceContext?: string;
        requiredConcepts?: string[];
        calibrationExamples?: Array<Record<string, unknown>>;
    }): Promise<JudgeResult>;
    private buildJudgePrompt;
}
//# sourceMappingURL=index.d.ts.map