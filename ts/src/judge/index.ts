/**
 * LLM-based judge for evaluating agent task outputs.
 * Port of autocontext/src/autocontext/execution/judge.py
 */

export { parseJudgeResponse } from "./parse.js";
export type { ParsedJudge, ParseMethod } from "./parse.js";
export { checkRubricCoherence } from "./rubric-coherence.js";
export type { RubricCoherenceResult } from "./rubric-coherence.js";
export { DelegatedJudge, CallbackJudge, SequentialDelegatedJudge } from "./delegated.js";
export type {
  DelegatedResult,
  CallbackEvaluateFn,
  EvaluateOpts as DelegatedEvaluateOpts,
  JudgeInterface,
} from "./delegated.js";
export {
  DEFAULT_FACTUAL_CONFIDENCE,
  detectGeneratedDimensions,
  LLMJudge,
} from "./llm-judge.js";
export type { LLMJudgeOpts } from "./llm-judge.js";
