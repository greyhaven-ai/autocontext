/**
 * Delegated judging — agent-as-judge pattern (AC-409).
 *
 * DelegatedJudge: accepts pre-computed evaluation results (no LLM call).
 * CallbackJudge: calls a user-supplied function for scoring.
 *
 * These allow autoctx to function as a pure control plane where the
 * calling agent provides evaluations, eliminating the need for autoctx
 * to have its own LLM access for judging.
 */

import type { JudgeResult } from "../types/index.js";

export interface DelegatedResult {
  score: number;
  reasoning: string;
  dimensionScores?: Record<string, number>;
}

export interface EvaluateOpts {
  taskPrompt: string;
  agentOutput: string;
  referenceContext?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Array<Record<string, unknown>>;
  pinnedDimensions?: string[];
}

/**
 * Judge that returns a pre-loaded result without calling any LLM.
 * Use when an external agent has already evaluated the output.
 */
export class DelegatedJudge {
  private result: DelegatedResult;
  readonly rubric: string;

  constructor(result: DelegatedResult, rubric = "(delegated — externally evaluated)") {
    this.result = result;
    this.rubric = rubric;
  }

  setResult(result: DelegatedResult): void {
    this.result = result;
  }

  async evaluate(_opts: EvaluateOpts): Promise<JudgeResult> {
    return {
      score: this.result.score,
      reasoning: this.result.reasoning,
      dimensionScores: this.result.dimensionScores ?? {},
      rawResponses: [],
      parseMethod: "delegated" as JudgeResult["parseMethod"],
      internalRetries: 0,
      dimensionsWereGenerated: false,
    };
  }
}

export type CallbackEvaluateFn = (opts: EvaluateOpts) => Promise<DelegatedResult>;

/**
 * Judge that delegates evaluation to a user-supplied callback function.
 * Use when the calling agent wants to provide scoring logic dynamically.
 */
export class CallbackJudge {
  private callback: CallbackEvaluateFn;
  readonly rubric: string;

  constructor(callback: CallbackEvaluateFn, rubric = "(callback — externally evaluated)") {
    this.callback = callback;
    this.rubric = rubric;
  }

  async evaluate(opts: EvaluateOpts): Promise<JudgeResult> {
    const result = await this.callback(opts);
    return {
      score: result.score,
      reasoning: result.reasoning,
      dimensionScores: result.dimensionScores ?? {},
      rawResponses: [],
      parseMethod: "callback" as JudgeResult["parseMethod"],
      internalRetries: 0,
      dimensionsWereGenerated: false,
    };
  }
}
