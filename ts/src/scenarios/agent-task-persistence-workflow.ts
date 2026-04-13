import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import type { AgentTaskSpec } from "./agent-task-spec.js";
import { getScenarioTypeMarker } from "./families.js";

export function buildPersistedAgentTaskSpecData(
  spec: AgentTaskSpec,
): Record<string, unknown> {
  const specData: Record<string, unknown> = {
    task_prompt: spec.taskPrompt,
    judge_rubric: spec.judgeRubric,
    output_format: spec.outputFormat,
    judge_model: spec.judgeModel,
  };
  if (spec.difficultyTiers) specData.difficulty_tiers = spec.difficultyTiers;
  if (spec.referenceContext) specData.reference_context = spec.referenceContext;
  if (spec.referenceSources) specData.reference_sources = spec.referenceSources;
  if (spec.requiredConcepts) specData.required_concepts = spec.requiredConcepts;
  if (spec.calibrationExamples) specData.calibration_examples = spec.calibrationExamples;
  if (spec.contextPreparation) specData.context_preparation = spec.contextPreparation;
  if (spec.requiredContextKeys) specData.required_context_keys = spec.requiredContextKeys;
  if (spec.maxRounds !== 1) specData.max_rounds = spec.maxRounds;
  if (spec.qualityThreshold !== 0.9) specData.quality_threshold = spec.qualityThreshold;
  if (spec.revisionPrompt) specData.revision_prompt = spec.revisionPrompt;
  if (spec.sampleInput) specData.sample_input = spec.sampleInput;
  return specData;
}

export function persistAgentTaskScenario(opts: {
  knowledgeRoot: string;
  name: string;
  spec: AgentTaskSpec;
}): string {
  const customDir = join(opts.knowledgeRoot, "_custom_scenarios");
  const scenarioDir = join(customDir, opts.name);
  if (!existsSync(scenarioDir)) {
    mkdirSync(scenarioDir, { recursive: true });
  }

  writeFileSync(
    join(scenarioDir, "agent_task_spec.json"),
    JSON.stringify(buildPersistedAgentTaskSpecData(opts.spec), null, 2),
    "utf-8",
  );
  writeFileSync(
    join(scenarioDir, "scenario_type.txt"),
    getScenarioTypeMarker("agent_task"),
    "utf-8",
  );

  return scenarioDir;
}
