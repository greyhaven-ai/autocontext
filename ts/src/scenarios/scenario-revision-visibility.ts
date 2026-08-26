export const IMMUTABLE_AGENT_TASK_PROMPT_SENTINEL =
  "[IMMUTABLE OPERATOR TASK PROMPT OMITTED]";

const IMMUTABLE_AGENT_TASK_PROMPT_KEYS = new Set(["taskPrompt", "task_prompt"]);

const LEGACY_EVALUATOR_ONLY_KEYS = new Set([
  "improvementTaskContractVersion",
  "improvement_task_contract_version",
  "judgeRubric",
  "judge_rubric",
  "rubric",
  "referenceContext",
  "reference_context",
  "referenceSources",
  "reference_sources",
  "requiredConcepts",
  "required_concepts",
  "calibrationExamples",
  "calibration_examples",
  "evaluationContext",
  "evaluation_context",
  "evaluationContextRef",
  "evaluation_context_ref",
  "difficultyTiers",
  "difficulty_tiers",
]);

const STRUCTURED_AGENT_TASK_EXECUTION_KEYS = new Set([
  "improvementTaskContractVersion",
  "improvement_task_contract_version",
  "taskDataSources",
  "task_data_sources",
  ...IMMUTABLE_AGENT_TASK_PROMPT_KEYS,
  "judgeRubric",
  "judge_rubric",
  "rubric",
  "outputFormat",
  "output_format",
  "judgeModel",
  "judge_model",
  "difficultyTiers",
  "difficulty_tiers",
  "sampleInput",
  "sample_input",
  "referenceContext",
  "reference_context",
  "referenceSources",
  "reference_sources",
  "evaluationContext",
  "evaluation_context",
  "evaluationContextRef",
  "evaluation_context_ref",
  "requiredConcepts",
  "required_concepts",
  "calibrationExamples",
  "calibration_examples",
  "contextPreparation",
  "context_preparation",
  "requiredContextKeys",
  "required_context_keys",
  "maxRounds",
  "max_rounds",
  "qualityThreshold",
  "quality_threshold",
  "revisionPrompt",
  "revision_prompt",
]);

const STRUCTURED_HIDDEN_EXECUTION_KEYS = new Set([
  "improvementTaskContractVersion",
  "improvement_task_contract_version",
  "taskDataSources",
  "task_data_sources",
  "sampleInput",
  "sample_input",
  ...LEGACY_EVALUATOR_ONLY_KEYS,
  "contextPreparation",
  "context_preparation",
  "requiredContextKeys",
  "required_context_keys",
]);

export interface ScenarioRevisionVisibility {
  providerVisibleSpec: Record<string, unknown>;
  immutableSpec: Record<string, unknown>;
  structuredAgentTask: boolean;
}

function isStructuredAgentTaskSpec(spec: Record<string, unknown>): boolean {
  return spec.improvementTaskContractVersion === 1
    || spec.improvement_task_contract_version === 1;
}

/**
 * Separate the executable prompt and assigned task data before an agent-task
 * spec is sent to a scenario-refinement provider. The prompt is represented by
 * a fixed sentinel so the provider can return a valid spec shape without ever
 * receiving or regenerating the operator's mission.
 */
export function partitionScenarioRevisionSpec(
  family: string,
  spec: Record<string, unknown>,
): ScenarioRevisionVisibility {
  if (family !== "agent_task") {
    return {
      providerVisibleSpec: { ...spec },
      immutableSpec: {},
      structuredAgentTask: false,
    };
  }

  const structuredAgentTask = isStructuredAgentTaskSpec(spec);
  const immutableKeys = structuredAgentTask
    ? STRUCTURED_AGENT_TASK_EXECUTION_KEYS
    : LEGACY_EVALUATOR_ONLY_KEYS;
  const providerVisibleSpec: Record<string, unknown> = {};
  const immutableSpec: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(spec)) {
    if (immutableKeys.has(key)) {
      immutableSpec[key] = value;
      if (structuredAgentTask && IMMUTABLE_AGENT_TASK_PROMPT_KEYS.has(key)) {
        providerVisibleSpec[key] = IMMUTABLE_AGENT_TASK_PROMPT_SENTINEL;
      } else if (structuredAgentTask && !STRUCTURED_HIDDEN_EXECUTION_KEYS.has(key)) {
        providerVisibleSpec[key] = value;
      }
    } else {
      providerVisibleSpec[key] = value;
    }
  }
  return { providerVisibleSpec, immutableSpec, structuredAgentTask };
}

/** Restore exact immutable fields and discard provider additions or mutations. */
export function restoreScenarioRevisionSpec(
  family: string,
  revisedSpec: Record<string, unknown>,
  immutableSpec: Record<string, unknown>,
): Record<string, unknown> {
  if (family !== "agent_task") return { ...revisedSpec };
  const immutableKeys = isStructuredAgentTaskSpec(immutableSpec)
    ? STRUCTURED_AGENT_TASK_EXECUTION_KEYS
    : LEGACY_EVALUATOR_ONLY_KEYS;
  const mutableRevisedSpec = Object.fromEntries(
    Object.entries(revisedSpec).filter(
      ([key]) => !immutableKeys.has(key),
    ),
  );
  return { ...mutableRevisedSpec, ...immutableSpec };
}
