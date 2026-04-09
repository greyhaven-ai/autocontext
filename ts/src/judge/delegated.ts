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

export interface JudgeInterface {
  readonly rubric: string;
  evaluate(opts: EvaluateOpts): Promise<JudgeResult>;
}

function toJudgeResult(
  result: DelegatedResult,
  parseMethod: "delegated" | "callback",
): JudgeResult {
  return {
    score: result.score,
    reasoning: result.reasoning,
    dimensionScores: result.dimensionScores ?? {},
    rawResponses: [],
    parseMethod,
    internalRetries: 0,
    dimensionsWereGenerated: false,
  };
}

/**
 * Judge that returns a pre-loaded result without calling any LLM.
 * Use when an external agent has already evaluated the output.
 */
export class DelegatedJudge implements JudgeInterface {
  #result: DelegatedResult;
  readonly rubric: string;

  constructor(result: DelegatedResult, rubric = "(delegated — externally evaluated)") {
    this.#result = result;
    this.rubric = rubric;
  }

  setResult(result: DelegatedResult): void {
    this.#result = result;
  }

  async evaluate(_opts: EvaluateOpts): Promise<JudgeResult> {
    return toJudgeResult(this.#result, "delegated");
  }
}

export type CallbackEvaluateFn = (opts: EvaluateOpts) => Promise<DelegatedResult>;

/**
 * Judge that delegates evaluation to a user-supplied callback function.
 * Use when the calling agent wants to provide scoring logic dynamically.
 */
export class CallbackJudge implements JudgeInterface {
  #callback: CallbackEvaluateFn;
  readonly rubric: string;

  constructor(callback: CallbackEvaluateFn, rubric = "(callback — externally evaluated)") {
    this.#callback = callback;
    this.rubric = rubric;
  }

  async evaluate(opts: EvaluateOpts): Promise<JudgeResult> {
    const result = await this.#callback(opts);
    return toJudgeResult(result, "callback");
  }
}

/**
 * Judge that consumes a precomputed sequence of delegated evaluations.
 * Each evaluate() call advances to the next supplied result.
 */
export class SequentialDelegatedJudge implements JudgeInterface {
  #index = 0;
  readonly rubric: string;
  readonly #results: DelegatedResult[];

  constructor(
    results: DelegatedResult[],
    rubric = "(delegated sequence — externally evaluated)",
  ) {
    this.#results = results;
    this.rubric = rubric;
  }

  async evaluate(_opts: EvaluateOpts): Promise<JudgeResult> {
    const current = this.#results[this.#index];
    if (!current) {
      throw new Error(`No delegated evaluation available for round ${this.#index + 1}`);
    }
    this.#index += 1;
    return toJudgeResult(current, "delegated");
  }
}
