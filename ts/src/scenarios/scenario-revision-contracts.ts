import type { LLMProvider } from "../types/index.js";

export interface RevisionResult {
  original: Record<string, unknown>;
  revised: Record<string, unknown>;
  changesApplied: boolean;
  error?: string;
}

export interface JudgeResult {
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
}

export interface RevisionPromptOpts {
  currentSpec: Record<string, unknown>;
  feedback: string;
  family: string;
  judgeResult?: JudgeResult;
}

export interface ReviseSpecOpts {
  currentSpec: Record<string, unknown>;
  feedback: string;
  family: string;
  provider: LLMProvider;
  model?: string;
  judgeResult?: JudgeResult;
}

export interface OutputRevisionOpts {
  originalOutput: string;
  judgeResult: JudgeResult;
  taskPrompt: string;
  revisionPrompt?: string;
  rubric?: string;
}
