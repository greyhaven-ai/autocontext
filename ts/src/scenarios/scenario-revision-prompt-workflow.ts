import type { ScenarioFamilyName } from "./families.js";
import type {
  OutputRevisionOpts,
  RevisionPromptOpts,
} from "./scenario-revision-contracts.js";

export const FAMILY_DESCRIPTIONS: Partial<Record<ScenarioFamilyName, string>> = {
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

export function buildWeakDimensionSection(
  dimensionScores: Record<string, number>,
): string | null {
  const weakDimensions = Object.entries(dimensionScores)
    .filter(([, score]) => score < 0.7)
    .sort(([, left], [, right]) => left - right);

  if (weakDimensions.length === 0) {
    return null;
  }

  const dimensionLines = weakDimensions
    .map(([dimension, score]) => `- ${dimension}: ${score.toFixed(2)}`)
    .join("\n");
  return `\n## Weak Dimensions (need improvement)\n${dimensionLines}`;
}

export function buildRevisionPrompt(opts: RevisionPromptOpts): string {
  const familyDescription = FAMILY_DESCRIPTIONS[opts.family as ScenarioFamilyName]
    ?? `a ${opts.family} scenario`;
  const sections: string[] = [
    `You are revising the spec for ${familyDescription}.`,
    "Given the current spec and user feedback, produce an updated JSON spec.",
    "Output ONLY the revised JSON object, no markdown fences or commentary.",
  ];

  if (opts.judgeResult) {
    sections.push(`\n## Current Score\n${opts.judgeResult.score.toFixed(2)}`);
    sections.push(`\n## Judge Reasoning\n${opts.judgeResult.reasoning}`);
    const weakDimensionSection = buildWeakDimensionSection(opts.judgeResult.dimensionScores);
    if (weakDimensionSection) {
      sections.push(weakDimensionSection);
    }
  }

  sections.push(
    `\n## Current Spec\n${JSON.stringify(opts.currentSpec, null, 2)}`,
    `\n## User Feedback\n${opts.feedback}`,
    "\n## Instructions",
    `Revise the ${opts.family} spec based on the feedback.`,
    "Preserve fields that aren't mentioned in the feedback.",
    "Output the complete revised spec as a JSON object.",
  );

  return sections.join("\n");
}

export function reviseAgentTaskOutput(opts: OutputRevisionOpts): string {
  const sections: string[] = [
    "You are revising your previous output based on judge feedback.",
    `\n## Current Score\n${opts.judgeResult.score.toFixed(2)}`,
    `\n## Judge Reasoning\n${opts.judgeResult.reasoning}`,
  ];

  const weakDimensionSection = buildWeakDimensionSection(opts.judgeResult.dimensionScores);
  if (weakDimensionSection) {
    sections.push(weakDimensionSection);
  }

  sections.push(`\n## Original Task\n${opts.taskPrompt}`);
  sections.push(`\n## Original Output\n${opts.originalOutput}`);

  if (opts.rubric) {
    sections.push(`\n## Rubric\n${opts.rubric}`);
  }

  if (opts.revisionPrompt) {
    sections.push(`\n## Revision Instructions\n${opts.revisionPrompt}`);
  }

  sections.push(
    "\n## Your Task",
    "Produce a revised, improved version of the output that addresses the judge's feedback and improves on the weak dimensions. Return ONLY the revised output, not commentary about the changes.",
  );

  return sections.join("\n");
}
