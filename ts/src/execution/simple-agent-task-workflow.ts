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
    userPrompt: buildSimpleAgentTaskUserPrompt({
      taskPrompt: opts.taskPrompt,
      referenceContext: opts.referenceContext,
      requiredConcepts: opts.requiredConcepts,
    }),
    model: opts.model,
  });
  return result.text;
}

export function buildSimpleAgentTaskUserPrompt(opts: {
  taskPrompt: string;
  referenceContext?: string;
  requiredConcepts?: string[];
}): string {
  const blocks = [
    opts.taskPrompt.trim(),
    buildReferenceContextBlock(opts.referenceContext),
    buildRequiredConceptsBlock(opts.requiredConcepts),
  ].filter((value) => value.length > 0);
  return blocks.join("\n\n");
}

export function buildSimpleAgentTaskRevisionPrompt(opts: {
  revisionPrompt?: string;
  output: string;
  judgeResult: AgentTaskResult;
  taskPrompt: string;
  referenceContext?: string;
  requiredConcepts?: string[];
}): string {
  const instruction = opts.revisionPrompt
    ?? "Revise the following output based on the judge's feedback. Maintain what works, fix what doesn't.";

  return [
    instruction,
    `## Original Output\n${opts.output}`,
    `## Judge Score: ${opts.judgeResult.score.toFixed(2)}`,
    `## Judge Feedback\n${opts.judgeResult.reasoning}`,
    buildReferenceContextBlock(opts.referenceContext),
    buildRequiredConceptsBlock(opts.requiredConcepts),
    `## Task\n${opts.taskPrompt}`,
    "Produce an improved version:",
  ].filter((value) => value.length > 0).join("\n\n");
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
      referenceContext: opts.referenceContext,
      requiredConcepts: opts.requiredConcepts,
    }),
    model: opts.model,
  });
  return result.text;
}

function buildReferenceContextBlock(referenceContext?: string): string {
  const trimmedReferenceContext = referenceContext?.trim();
  return trimmedReferenceContext ? `## Reference Context\n${trimmedReferenceContext}` : "";
}

function buildRequiredConceptsBlock(requiredConcepts?: string[]): string {
  const normalizedConcepts = requiredConcepts
    ?.map((concept) => concept.trim())
    .filter((concept) => concept.length > 0);
  return normalizedConcepts && normalizedConcepts.length > 0
    ? `## Required Concepts\n${normalizedConcepts.map((concept) => `- ${concept}`).join("\n")}`
    : "";
}
