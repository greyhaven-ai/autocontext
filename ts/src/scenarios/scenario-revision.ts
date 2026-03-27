/**
 * Scenario revision flow — iterative spec refinement with feedback (AC-441).
 *
 * Ports Python's agent_task_revision.py revision prompt building and adds
 * a generic reviseSpec() that works for all families. Users can create a
 * scenario, see the result, provide feedback, and get an improved version
 * without starting over.
 *
 * Two levels of revision:
 * 1. Spec revision (reviseSpec) — refine the scenario definition itself
 * 2. Output revision (reviseAgentTaskOutput) — refine agent output based on judge feedback
 */

import type { LLMProvider } from "../types/index.js";
import type { ScenarioFamilyName } from "./families.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RevisionResult {
  /** The original spec (preserved for diff/audit) */
  original: Record<string, unknown>;
  /** The revised spec */
  revised: Record<string, unknown>;
  /** Whether changes were actually applied */
  changesApplied: boolean;
  /** Error message if revision failed */
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

// ---------------------------------------------------------------------------
// Revision prompt building
// ---------------------------------------------------------------------------

const FAMILY_DESCRIPTIONS: Partial<Record<ScenarioFamilyName, string>> = {
  agent_task: "an agent task evaluated by an LLM judge",
  simulation: "a simulation with action traces and environment state",
  artifact_editing: "an artifact editing scenario with file modifications",
  investigation: "an investigation with evidence gathering and diagnosis",
  workflow: "a transactional workflow with compensation and side effects",
  negotiation: "a negotiation with hidden preferences and opponent modeling",
  schema_evolution: "a schema evolution scenario with migrations and stale context",
  tool_fragility: "a tool fragility scenario with API drift and adaptation",
  operator_loop: "an operator-in-the-loop scenario with escalation judgment",
  coordination: "a multi-agent coordination scenario with handoffs and merges",
};

/**
 * Build a revision prompt from current spec + user feedback.
 */
export function buildRevisionPrompt(opts: RevisionPromptOpts): string {
  const { currentSpec, feedback, family, judgeResult } = opts;
  const familyDesc = FAMILY_DESCRIPTIONS[family as ScenarioFamilyName] ?? `a ${family} scenario`;

  const sections: string[] = [];

  sections.push(
    `You are revising the spec for ${familyDesc}.`,
    "Given the current spec and user feedback, produce an updated JSON spec.",
    "Output ONLY the revised JSON object, no markdown fences or commentary.",
  );

  if (judgeResult) {
    sections.push(`\n## Current Score\n${judgeResult.score.toFixed(2)}`);
    sections.push(`\n## Judge Reasoning\n${judgeResult.reasoning}`);

    const weakDims = Object.entries(judgeResult.dimensionScores)
      .filter(([, score]) => score < 0.7)
      .sort(([, a], [, b]) => a - b);

    if (weakDims.length > 0) {
      const dimLines = weakDims.map(([dim, score]) => `- ${dim}: ${score.toFixed(2)}`).join("\n");
      sections.push(`\n## Weak Dimensions (need improvement)\n${dimLines}`);
    }
  }

  sections.push(
    `\n## Current Spec\n${JSON.stringify(currentSpec, null, 2)}`,
    `\n## User Feedback\n${feedback}`,
    "\n## Instructions",
    `Revise the ${family} spec based on the feedback.`,
    "Preserve fields that aren't mentioned in the feedback.",
    "Output the complete revised spec as a JSON object.",
  );

  return sections.join("\n");
}

// ---------------------------------------------------------------------------
// Spec revision
// ---------------------------------------------------------------------------

function parseJsonFromLLMResponse(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();

  // Try direct parse
  try {
    return JSON.parse(trimmed);
  } catch { /* continue */ }

  // Try extracting JSON from markdown fences or surrounding text
  const jsonStart = trimmed.indexOf("{");
  const jsonEnd = trimmed.lastIndexOf("}");
  if (jsonStart !== -1 && jsonEnd > jsonStart) {
    try {
      return JSON.parse(trimmed.slice(jsonStart, jsonEnd + 1));
    } catch { /* continue */ }
  }

  return null;
}

/**
 * Revise a scenario spec using LLM + user feedback.
 *
 * Takes the current spec, user feedback, and optionally judge results,
 * and produces an updated spec. On LLM failure, returns the original
 * spec with changesApplied: false and an error message.
 */
export async function reviseSpec(opts: ReviseSpecOpts): Promise<RevisionResult> {
  const { currentSpec, feedback, family, provider, model, judgeResult } = opts;
  const original = { ...currentSpec };

  const prompt = buildRevisionPrompt({ currentSpec, feedback, family, judgeResult });

  try {
    const result = await provider.complete({
      systemPrompt: `You are a scenario designer. Revise the ${family} spec based on user feedback. Output only valid JSON.`,
      userPrompt: prompt,
      ...(model ? { model } : {}),
    });

    const revised = parseJsonFromLLMResponse(result.text);
    if (!revised) {
      return {
        original,
        revised: original,
        changesApplied: false,
        error: "LLM response was not valid JSON",
      };
    }

    // Merge: use revised values but keep any fields from original not in revised
    const merged = { ...original, ...revised };

    return {
      original,
      revised: merged,
      changesApplied: true,
    };
  } catch (err) {
    return {
      original,
      revised: original,
      changesApplied: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

// ---------------------------------------------------------------------------
// Agent task output revision (ported from Python's build_revision_prompt)
// ---------------------------------------------------------------------------

/**
 * Build a revision prompt for an agent's output based on judge feedback.
 * Used in the ImprovementLoop to request better output from the agent.
 *
 * Ports Python's agent_task_revision.build_revision_prompt().
 */
export function reviseAgentTaskOutput(opts: OutputRevisionOpts): string {
  const { originalOutput, judgeResult, taskPrompt, revisionPrompt, rubric } = opts;

  const sections: string[] = [];

  sections.push("You are revising your previous output based on judge feedback.");
  sections.push(`\n## Current Score\n${judgeResult.score.toFixed(2)}`);
  sections.push(`\n## Judge Reasoning\n${judgeResult.reasoning}`);

  // Highlight weak dimensions
  const weakDims = Object.entries(judgeResult.dimensionScores)
    .filter(([, score]) => score < 0.7)
    .sort(([, a], [, b]) => a - b);

  if (weakDims.length > 0) {
    const dimLines = weakDims.map(([dim, score]) => `- ${dim}: ${score.toFixed(2)}`).join("\n");
    sections.push(`\n## Weak Dimensions (need improvement)\n${dimLines}`);
  }

  sections.push(`\n## Original Task\n${taskPrompt}`);
  sections.push(`\n## Original Output\n${originalOutput}`);

  if (rubric) {
    sections.push(`\n## Rubric\n${rubric}`);
  }

  if (revisionPrompt) {
    sections.push(`\n## Revision Instructions\n${revisionPrompt}`);
  }

  sections.push(
    "\n## Your Task",
    "Produce a revised, improved version of the output that addresses " +
    "the judge's feedback and improves on the weak dimensions. " +
    "Return ONLY the revised output, not commentary about the changes.",
  );

  return sections.join("\n");
}
