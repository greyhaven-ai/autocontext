/**
 * AgentTaskDesigner — generates AgentTaskSpec from natural language.
 * Port of mts/src/mts/scenarios/custom/agent_task_designer.py
 */

import type { AgentTaskSpec } from "./agent-task-spec.js";
import { parseRawSpec } from "./agent-task-spec.js";

export const SPEC_START = "<!-- AGENT_TASK_SPEC_START -->";
export const SPEC_END = "<!-- AGENT_TASK_SPEC_END -->";

const EXAMPLE_SPEC = {
  task_prompt:
    "Write a Python function that takes a list of integers and returns " +
    "the second largest unique value. Handle edge cases like empty lists " +
    "and lists with fewer than two unique values.",
  judge_rubric:
    "Evaluate on: (1) Correctness — does the function return the right answer " +
    "for normal and edge cases? (2) Code quality — is it readable, well-named, " +
    "and idiomatic Python? (3) Edge case handling — does it handle empty lists, " +
    "single-element lists, and duplicate values gracefully?",
  output_format: "code",
  judge_model: "claude-sonnet-4-20250514",
  difficulty_tiers: null,
  reference_context: null,
  reference_sources: null,
  required_concepts: null,
  context_preparation: null,
  required_context_keys: null,
  max_rounds: 1,
  quality_threshold: 0.9,
  revision_prompt: null,
};

export const AGENT_TASK_DESIGNER_SYSTEM = `You are a scenario designer for MTS, an agent evaluation system. \
Given a natural language description, produce an AgentTaskSpec JSON \
that defines a task prompt, evaluation rubric, output format, and judge model.

The output must be valid JSON wrapped in delimiters:
${SPEC_START}
{ ... }
${SPEC_END}

## AgentTaskSpec Schema

\`\`\`json
{
  "task_prompt": "The full prompt given to the agent being evaluated",
  "judge_rubric": "Detailed rubric for the LLM judge to score the output",
  "output_format": "free_text | json_schema | code",
  "judge_model": "claude-sonnet-4-20250514",
  "difficulty_tiers": null,
  "reference_context": "Authoritative domain knowledge for judging factual accuracy (optional)",
  "reference_sources": ["list of source URLs or references (optional)"],
  "required_concepts": ["key concepts the output must correctly address (optional)"],
  "context_preparation": "Instructions for gathering context before generation (optional)",
  "required_context_keys": ["state keys that must be present after context preparation (optional)"],
  "max_rounds": 1,
  "quality_threshold": 0.9,
  "revision_prompt": "Instructions for revising output based on judge feedback (optional)"
}
\`\`\`

## Rules

- \`task_prompt\` must be clear, detailed, and self-contained
- \`judge_rubric\` must list specific evaluation dimensions with criteria
- \`output_format\` must be one of: free_text, json_schema, code
- \`judge_model\` should be a valid model identifier
- \`max_rounds\` (optional, default 1) — maximum improvement rounds
- \`quality_threshold\` (optional, default 0.9) — stop improving when score >= this

## Example

${SPEC_START}
${JSON.stringify(EXAMPLE_SPEC, null, 2)}
${SPEC_END}

Now design an agent task scenario for the user's description.
`;

/**
 * Parse an AgentTaskSpec from LLM response text containing delimiters.
 */
export function parseAgentTaskSpec(text: string): AgentTaskSpec {
  const startIdx = text.indexOf(SPEC_START);
  const endIdx = text.indexOf(SPEC_END);
  if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) {
    throw new Error("response does not contain AGENT_TASK_SPEC delimiters");
  }
  const raw = text.slice(startIdx + SPEC_START.length, endIdx).trim();
  const data = JSON.parse(raw) as Record<string, unknown>;
  return parseRawSpec(data);
}

/**
 * Design an agent task spec from a natural language description.
 */
export async function designAgentTask(
  description: string,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<AgentTaskSpec> {
  const userPrompt = `User description:\n${description}`;
  const response = await llmFn(AGENT_TASK_DESIGNER_SYSTEM, userPrompt);
  return parseAgentTaskSpec(response);
}
