import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import type { AgentTaskSpec } from "./agent-task-spec.js";

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

  writeFileSync(join(opts.scenarioDir, "scenario_type.txt"), opts.scenarioType, "utf-8");
  writeFileSync(
    join(opts.scenarioDir, "spec.json"),
    JSON.stringify(opts.persistedSpec, null, 2),
    "utf-8",
  );

  if (opts.family === opts.agentTaskFamily && opts.agentTaskSpec) {
    writeFileSync(
      join(opts.scenarioDir, "agent_task_spec.json"),
      JSON.stringify(
        {
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
          max_rounds: opts.agentTaskSpec.maxRounds,
          quality_threshold: opts.agentTaskSpec.qualityThreshold,
          revision_prompt: opts.agentTaskSpec.revisionPrompt ?? null,
          sample_input: opts.agentTaskSpec.sampleInput ?? null,
          reference_context: opts.agentTaskSpec.referenceContext ?? null,
          evaluation_context: opts.agentTaskSpec.evaluationContext ?? null,
          reference_sources: opts.agentTaskSpec.referenceSources ?? null,
          required_concepts: opts.agentTaskSpec.requiredConcepts ?? null,
          calibration_examples: opts.agentTaskSpec.calibrationExamples ?? null,
          context_preparation: opts.agentTaskSpec.contextPreparation ?? null,
          required_context_keys: opts.agentTaskSpec.requiredContextKeys ?? null,
          difficulty_tiers: opts.agentTaskSpec.difficultyTiers ?? null,
        },
        null,
        2,
      ),
      "utf-8",
    );
    rmSync(join(opts.scenarioDir, "scenario.js"), { force: true });
    return;
  }

  if (opts.source) {
    rmSync(join(opts.scenarioDir, "agent_task_spec.json"), { force: true });
    writeFileSync(join(opts.scenarioDir, "scenario.js"), opts.source, "utf-8");
  }
}
