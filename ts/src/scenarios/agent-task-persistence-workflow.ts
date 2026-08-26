import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import type { AgentTaskSpec } from "./agent-task-spec.js";
import { getScenarioTypeMarker } from "./families.js";
import {
  evaluationContextReference,
  persistPrivateEvaluatorContext,
  prunePrivateEvaluatorContexts,
  withPrivateEvaluatorContextWriteLock,
} from "./private-evaluator-context-store.js";

export function buildPersistedAgentTaskSpecData(spec: AgentTaskSpec): Record<string, unknown> {
  const specData: Record<string, unknown> = {
    task_prompt: spec.taskPrompt,
    judge_rubric: spec.judgeRubric,
    output_format: spec.outputFormat,
    judge_model: spec.judgeModel,
  };
  if (spec.improvementTaskContractVersion) {
    specData.improvement_task_contract_version = spec.improvementTaskContractVersion;
  }
  if (spec.taskDataSources !== undefined) {
    specData.task_data_sources = spec.taskDataSources;
  }
  if (spec.difficultyTiers) specData.difficulty_tiers = spec.difficultyTiers;
  if (spec.referenceContext) specData.reference_context = spec.referenceContext;
  if (spec.evaluationContext?.trim()) {
    specData.evaluation_context_ref = evaluationContextReference(spec.evaluationContext);
  }
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

  withPrivateEvaluatorContextWriteLock({
    knowledgeRoot: opts.knowledgeRoot,
    scenarioName: opts.name,
    write: () => {
      const evaluationContextRef = opts.spec.evaluationContext?.trim()
        ? evaluationContextReference(opts.spec.evaluationContext)
        : undefined;

      // eval -> none is deliberately fail-closed: remove every old/faulted
      // private record while the old public reference is still active.
      if (!evaluationContextRef) {
        prunePrivateEvaluatorContexts({
          knowledgeRoot: opts.knowledgeRoot,
          scenarioName: opts.name,
        });
      }

      // Publish the reference before writing its secret. A crash in between
      // leaves a dangling ref, which the loader rejects, rather than a usable
      // no-evaluator task beside an orphaned private record.
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

      if (evaluationContextRef) {
        persistPrivateEvaluatorContext({
          knowledgeRoot: opts.knowledgeRoot,
          scenarioName: opts.name,
          evaluationContext: opts.spec.evaluationContext,
        });
      }

      rmSync(join(scenarioDir, "spec.json"), { force: true });
      rmSync(join(scenarioDir, "scenario.js"), { force: true });
      if (evaluationContextRef) {
        prunePrivateEvaluatorContexts({
          knowledgeRoot: opts.knowledgeRoot,
          scenarioName: opts.name,
          keepReference: evaluationContextRef,
        });
      }
    },
  });

  return scenarioDir;
}
