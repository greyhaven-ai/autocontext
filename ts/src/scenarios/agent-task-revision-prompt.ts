import type { AgentTaskResult } from "../types/index.js";

export interface AgentTaskRevisionPromptOpts {
  revisionPrompt?: string | null;
  output: string;
  judgeResult: AgentTaskResult;
  taskPrompt: string;
  judgeRubric: string;
  referenceContext?: string | null;
  referenceSources?: readonly string[] | null;
  requiredConcepts?: readonly string[] | null;
  sampleInput?: string | null;
}

/**
 * Build a revision prompt that keeps the candidate grounded in the same task,
 * evidence, and evaluation contract used to generate and judge it.
 */
export function buildAgentTaskRevisionPrompt(opts: AgentTaskRevisionPromptOpts): string {
  const instruction =
    normalizeText(opts.revisionPrompt) ??
    "Revise the following output based on the judge's feedback. Maintain what works, fix what doesn't.";

  return [
    instruction,
    `## Original Output\n${opts.output}`,
    `## Judge Score: ${opts.judgeResult.score.toFixed(2)}`,
    `## Judge Feedback\n${opts.judgeResult.reasoning}`,
    buildTextBlock("Evaluation Criteria", opts.judgeRubric),
    buildTextBlock("Reference Context", opts.referenceContext),
    buildListBlock("Reference Sources", opts.referenceSources),
    buildListBlock("Required Concepts", opts.requiredConcepts),
    buildTextBlock("Input Data", opts.sampleInput),
    buildTextBlock("Task", opts.taskPrompt),
    "Produce an improved version:",
  ]
    .filter((value) => value.length > 0)
    .join("\n\n");
}

function buildTextBlock(title: string, value?: string | null): string {
  const normalized = normalizeText(value);
  return normalized ? `## ${title}\n${normalized}` : "";
}

function buildListBlock(title: string, values?: readonly string[] | null): string {
  const normalized = values
    ?.map((value) => value.trim())
    .filter((value) => value.length > 0);
  return normalized && normalized.length > 0
    ? `## ${title}\n${normalized.map((value) => `- ${value}`).join("\n")}`
    : "";
}

function normalizeText(value?: string | null): string | undefined {
  const normalized = value?.trim();
  return normalized ? normalized : undefined;
}
