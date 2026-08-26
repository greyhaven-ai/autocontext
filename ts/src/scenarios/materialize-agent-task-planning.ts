import type { AgentTaskSpec } from "./agent-task-spec.js";

export function buildAgentTaskMaterializeInput(
  healedSpec: Record<string, unknown>,
): Record<string, unknown> {
  return {
    improvementTaskContractVersion:
      healedSpec.improvementTaskContractVersion ?? healedSpec.improvement_task_contract_version,
    taskDataSources: healedSpec.taskDataSources ?? healedSpec.task_data_sources,
    taskPrompt: String(healedSpec.taskPrompt ?? healedSpec.task_prompt ?? ""),
    judgeRubric: String(
      healedSpec.judgeRubric ?? healedSpec.judge_rubric ?? healedSpec.rubric ?? "",
    ),
    outputFormat: healedSpec.outputFormat ?? healedSpec.output_format ?? "free_text",
    judgeModel: healedSpec.judgeModel ?? healedSpec.judge_model ?? "",
    difficultyTiers: healedSpec.difficultyTiers ?? healedSpec.difficulty_tiers ?? null,
    referenceContext: healedSpec.referenceContext ?? healedSpec.reference_context ?? null,
    evaluationContext: healedSpec.evaluationContext ?? healedSpec.evaluation_context ?? null,
    referenceSources: healedSpec.referenceSources ?? healedSpec.reference_sources ?? null,
    requiredConcepts: healedSpec.requiredConcepts ?? healedSpec.required_concepts ?? null,
    calibrationExamples: healedSpec.calibrationExamples ?? healedSpec.calibration_examples ?? null,
    contextPreparation: healedSpec.contextPreparation ?? healedSpec.context_preparation ?? null,
    requiredContextKeys: healedSpec.requiredContextKeys ?? healedSpec.required_context_keys ?? null,
    maxRounds: healedSpec.maxRounds ?? healedSpec.max_rounds ?? 1,
    qualityThreshold: healedSpec.qualityThreshold ?? healedSpec.quality_threshold ?? 0.9,
    revisionPrompt: healedSpec.revisionPrompt ?? healedSpec.revision_prompt ?? null,
    sampleInput: healedSpec.sampleInput ?? healedSpec.sample_input ?? null,
  };
}

export function buildAgentTaskPersistedSpecFields(
  agentTaskSpec: AgentTaskSpec,
): Record<string, unknown> {
  return {
    ...(agentTaskSpec.improvementTaskContractVersion === undefined
      ? {}
      : {
          improvementTaskContractVersion: agentTaskSpec.improvementTaskContractVersion,
        }),
    ...(agentTaskSpec.taskDataSources === undefined
      ? {}
      : { taskDataSources: agentTaskSpec.taskDataSources }),
    taskPrompt: agentTaskSpec.taskPrompt,
    judgeRubric: agentTaskSpec.judgeRubric,
    rubric: agentTaskSpec.judgeRubric,
    outputFormat: agentTaskSpec.outputFormat,
    judgeModel: agentTaskSpec.judgeModel,
    difficultyTiers: agentTaskSpec.difficultyTiers ?? null,
    referenceContext: agentTaskSpec.referenceContext ?? null,
    evaluationContext: agentTaskSpec.evaluationContext ?? null,
    referenceSources: agentTaskSpec.referenceSources ?? null,
    requiredConcepts: agentTaskSpec.requiredConcepts ?? null,
    calibrationExamples: agentTaskSpec.calibrationExamples ?? null,
    contextPreparation: agentTaskSpec.contextPreparation ?? null,
    requiredContextKeys: agentTaskSpec.requiredContextKeys ?? null,
    maxRounds: agentTaskSpec.maxRounds,
    qualityThreshold: agentTaskSpec.qualityThreshold,
    revisionPrompt: agentTaskSpec.revisionPrompt ?? null,
    sampleInput: agentTaskSpec.sampleInput ?? null,
  };
}
