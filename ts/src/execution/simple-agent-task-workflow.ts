import { LLMJudge } from "../judge/llm-judge.js";
import type { AgentTaskResult, LLMProvider } from "../types/index.js";
import type { JudgeInterface } from "../judge/delegated.js";
import type { RlmSessionRecord, RlmTaskConfig } from "../rlm/types.js";
import { runAgentTaskRlmSession } from "../rlm/agent-task.js";

export interface EvaluateSimpleAgentTaskOpts {
  taskPrompt: string;
  rubric: string;
  provider: LLMProvider;
  model: string;
  output: string;
  judgeOverride?: JudgeInterface;
  referenceContext?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Array<Record<string, unknown>>;
  pinnedDimensions?: string[];
}

export async function evaluateSimpleAgentTaskOutput(
  opts: EvaluateSimpleAgentTaskOpts,
): Promise<AgentTaskResult> {
  const judge = opts.judgeOverride ?? new LLMJudge({
    provider: opts.provider,
    model: opts.model,
    rubric: opts.rubric,
  });
  const result = await judge.evaluate({
    taskPrompt: opts.taskPrompt,
    agentOutput: opts.output,
    referenceContext: opts.referenceContext,
    requiredConcepts: opts.requiredConcepts,
    calibrationExamples: opts.calibrationExamples,
    pinnedDimensions: opts.pinnedDimensions,
  });
  return {
    score: result.score,
    reasoning: result.reasoning,
    dimensionScores: result.dimensionScores,
    internalRetries: result.internalRetries ?? 0,
  };
}

export async function runSimpleAgentTaskRlm(opts: {
  provider: LLMProvider;
  model: string;
  config: RlmTaskConfig | null;
  phase: "generate" | "revise";
  taskPrompt: string;
  rubric: string;
  sessions: RlmSessionRecord[];
  revisionPrompt?: string;
  currentOutput?: string;
  judgeResult?: AgentTaskResult;
  referenceContext?: string;
  requiredConcepts?: string[];
}): Promise<string | null> {
  if (!opts.config) {
    return null;
  }
  const record = await runAgentTaskRlmSession({
    provider: opts.provider,
    model: opts.model,
    config: opts.config,
    phase: opts.phase,
    taskPrompt: opts.taskPrompt,
    rubric: opts.rubric,
    currentOutput: opts.currentOutput,
    judgeResult: opts.judgeResult,
    referenceContext: opts.referenceContext,
    requiredConcepts: opts.requiredConcepts,
    revisionPrompt: opts.revisionPrompt,
  });
  opts.sessions.push(record);
  const content = record.content.trim();
  return content.length > 0 ? content : null;
}

export async function generateSimpleAgentTaskOutput(opts: {
  provider: LLMProvider;
  model: string;
  taskPrompt: string;
  rubric: string;
  rlmConfig: RlmTaskConfig | null;
  rlmSessions: RlmSessionRecord[];
  referenceContext?: string;
  requiredConcepts?: string[];
}): Promise<string> {
  const rlmOutput = await runSimpleAgentTaskRlm({
    provider: opts.provider,
    model: opts.model,
    config: opts.rlmConfig,
    phase: "generate",
    taskPrompt: opts.taskPrompt,
    rubric: opts.rubric,
    sessions: opts.rlmSessions,
    referenceContext: opts.referenceContext,
    requiredConcepts: opts.requiredConcepts,
  });
  if (rlmOutput) {
    return rlmOutput;
  }

  const result = await opts.provider.complete({
    systemPrompt: "You are a skilled writer and analyst. Complete the task precisely.",
    userPrompt: opts.taskPrompt,
    model: opts.model,
  });
  return result.text;
}

export function buildSimpleAgentTaskRevisionPrompt(opts: {
  revisionPrompt?: string;
  output: string;
  judgeResult: AgentTaskResult;
  taskPrompt: string;
}): string {
  const instruction = opts.revisionPrompt
    ?? "Revise the following output based on the judge's feedback. Maintain what works, fix what doesn't.";

  return (
    `${instruction}\n\n` +
    `## Original Output\n${opts.output}\n\n` +
    `## Judge Score: ${opts.judgeResult.score.toFixed(2)}\n` +
    `## Judge Feedback\n${opts.judgeResult.reasoning}\n\n` +
    `## Task\n${opts.taskPrompt}\n\n` +
    "Produce an improved version:"
  );
}

export async function reviseSimpleAgentTaskOutput(opts: {
  provider: LLMProvider;
  model: string;
  taskPrompt: string;
  rubric: string;
  revisionPrompt?: string;
  output: string;
  judgeResult: AgentTaskResult;
  rlmConfig: RlmTaskConfig | null;
  rlmSessions: RlmSessionRecord[];
  referenceContext?: string;
  requiredConcepts?: string[];
}): Promise<string> {
  const rlmOutput = await runSimpleAgentTaskRlm({
    provider: opts.provider,
    model: opts.model,
    config: opts.rlmConfig,
    phase: "revise",
    taskPrompt: opts.taskPrompt,
    rubric: opts.rubric,
    sessions: opts.rlmSessions,
    revisionPrompt: opts.revisionPrompt,
    currentOutput: opts.output,
    judgeResult: opts.judgeResult,
    referenceContext: opts.referenceContext,
    requiredConcepts: opts.requiredConcepts,
  });
  if (rlmOutput) {
    return rlmOutput;
  }

  const result = await opts.provider.complete({
    systemPrompt:
      "You are revising content based on expert feedback. Improve the output. " +
      "IMPORTANT: Return ONLY the revised content. Do NOT include analysis, " +
      "explanations, headers like '## Revised Output', or self-assessment. " +
      "Just output the improved version directly.",
    userPrompt: buildSimpleAgentTaskRevisionPrompt({
      revisionPrompt: opts.revisionPrompt,
      output: opts.output,
      judgeResult: opts.judgeResult,
      taskPrompt: opts.taskPrompt,
    }),
    model: opts.model,
  });
  return result.text;
}
