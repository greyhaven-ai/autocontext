import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";

import type { AgentTaskSpec } from "./agent-task-spec.js";
import {
  evaluationContextReference,
  knowledgeRootForScenarioDirectory,
  persistPrivateEvaluatorContext,
  prunePrivateEvaluatorContexts,
  tryKnowledgeRootForScenarioDirectory,
  withPrivateEvaluatorContextWriteLock,
  withoutPersistedEvaluatorPlaintext,
} from "./private-evaluator-context-store.js";

export interface MaterializedArtifactPersistenceRequest {
  scenarioDir: string;
  scenarioType: string;
  persistedSpec: Record<string, unknown>;
  family: string;
  agentTaskFamily: string;
  agentTaskSpec: AgentTaskSpec | null;
  source: string | null;
}

export function persistMaterializedScenarioArtifacts(
  opts: MaterializedArtifactPersistenceRequest,
): void {
  if (!existsSync(opts.scenarioDir)) {
    mkdirSync(opts.scenarioDir, { recursive: true });
  }

  const publicPersistedSpec = withoutPersistedEvaluatorPlaintext(opts.persistedSpec);
  delete publicPersistedSpec.evaluationContextRef;
  delete publicPersistedSpec.evaluation_context_ref;
  const evaluatorContext =
    opts.family === opts.agentTaskFamily ? opts.agentTaskSpec?.evaluationContext : undefined;
  const knowledgeRoot = evaluatorContext
    ? knowledgeRootForScenarioDirectory(opts.scenarioDir)
    : tryKnowledgeRootForScenarioDirectory(opts.scenarioDir);
  const scenarioName = basename(opts.scenarioDir);
  const evaluationContextRef = evaluatorContext?.trim()
    ? evaluationContextReference(evaluatorContext)
    : undefined;
  if (evaluationContextRef) {
    publicPersistedSpec.evaluationContextRef = evaluationContextRef;
  }

  const persist = () => {
    // For eval -> none/family transitions, remove every old/faulted private
    // record while the old public evaluator ref is still active.
    if (knowledgeRoot && !evaluationContextRef) {
      prunePrivateEvaluatorContexts({
        knowledgeRoot,
        scenarioName,
      });
    }

    if (opts.family === opts.agentTaskFamily && opts.agentTaskSpec) {
      const publicAgentTaskSpec = {
        ...(opts.agentTaskSpec.improvementTaskContractVersion === undefined
          ? {}
          : {
              improvement_task_contract_version:
                opts.agentTaskSpec.improvementTaskContractVersion,
            }),
        ...(opts.agentTaskSpec.taskDataSources === undefined
          ? {}
          : { task_data_sources: opts.agentTaskSpec.taskDataSources }),
        task_prompt: opts.agentTaskSpec.taskPrompt,
        judge_rubric: opts.agentTaskSpec.judgeRubric,
        output_format: opts.agentTaskSpec.outputFormat,
        judge_model: opts.agentTaskSpec.judgeModel,
        min_rounds: opts.agentTaskSpec.minRounds ?? 1,
        max_rounds: opts.agentTaskSpec.maxRounds,
        quality_threshold: opts.agentTaskSpec.qualityThreshold,
        revision_prompt: opts.agentTaskSpec.revisionPrompt ?? null,
        sample_input: opts.agentTaskSpec.sampleInput ?? null,
        reference_context: opts.agentTaskSpec.referenceContext ?? null,
        ...(evaluationContextRef ? { evaluation_context_ref: evaluationContextRef } : {}),
        reference_sources: opts.agentTaskSpec.referenceSources ?? null,
        required_concepts: opts.agentTaskSpec.requiredConcepts ?? null,
        calibration_examples: opts.agentTaskSpec.calibrationExamples ?? null,
        context_preparation: opts.agentTaskSpec.contextPreparation ?? null,
        required_context_keys: opts.agentTaskSpec.requiredContextKeys ?? null,
        difficulty_tiers: opts.agentTaskSpec.difficultyTiers ?? null,
      };

      // Publish both public refs and the agent-task marker before the private
      // record. Any crash before the private write therefore reloads as a
      // missing-ref error instead of exposing an orphaned secret to tools.
      writeFileSync(
        join(opts.scenarioDir, "agent_task_spec.json"),
        JSON.stringify(publicAgentTaskSpec, null, 2),
        "utf-8",
      );
      writeFileSync(
        join(opts.scenarioDir, "spec.json"),
        JSON.stringify(publicPersistedSpec, null, 2),
        "utf-8",
      );
      writeFileSync(join(opts.scenarioDir, "scenario_type.txt"), opts.scenarioType, "utf-8");

      if (knowledgeRoot && evaluationContextRef) {
        persistPrivateEvaluatorContext({
          knowledgeRoot,
          scenarioName,
          evaluationContext: evaluatorContext,
        });
      }
      rmSync(join(opts.scenarioDir, "scenario.js"), { force: true });
      if (knowledgeRoot && evaluationContextRef) {
        prunePrivateEvaluatorContexts({
          knowledgeRoot,
          scenarioName,
          keepReference: evaluationContextRef,
        });
      }
      return;
    }

    writeFileSync(join(opts.scenarioDir, "scenario_type.txt"), opts.scenarioType, "utf-8");
    writeFileSync(
      join(opts.scenarioDir, "spec.json"),
      JSON.stringify(publicPersistedSpec, null, 2),
      "utf-8",
    );
    rmSync(join(opts.scenarioDir, "agent_task_spec.json"), { force: true });
    if (opts.source) {
      writeFileSync(join(opts.scenarioDir, "scenario.js"), opts.source, "utf-8");
    }
  };

  if (knowledgeRoot) {
    withPrivateEvaluatorContextWriteLock({ knowledgeRoot, scenarioName, write: persist });
  } else {
    persist();
  }
}
