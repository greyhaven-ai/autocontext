export const IMMUTABLE_AGENT_TASK_PROMPT_SENTINEL =
  "[IMMUTABLE OPERATOR TASK PROMPT OMITTED]";

const IMMUTABLE_AGENT_TASK_PROMPT_KEYS = new Set([
  "taskPrompt",
  "task_prompt",
]);

const IMMUTABLE_AGENT_TASK_KEYS = new Set([
  ...IMMUTABLE_AGENT_TASK_PROMPT_KEYS,
  "improvementTaskContractVersion",
  "improvement_task_contract_version",
  "taskDataSources",
  "task_data_sources",
  "sampleInput",
  "sample_input",
  "referenceContext",
  "reference_context",
  "referenceSources",
  "reference_sources",
  "evaluationContext",
  "evaluation_context",
  "contextPreparation",
  "context_preparation",
  "requiredContextKeys",
  "required_context_keys",
]);

export interface ScenarioRevisionVisibility {
  providerVisibleSpec: Record<string, unknown>;
  immutableSpec: Record<string, unknown>;
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
    return { providerVisibleSpec: { ...spec }, immutableSpec: {} };
  }

  const providerVisibleSpec: Record<string, unknown> = {};
  const immutableSpec: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(spec)) {
    if (IMMUTABLE_AGENT_TASK_KEYS.has(key)) {
      immutableSpec[key] = value;
      if (IMMUTABLE_AGENT_TASK_PROMPT_KEYS.has(key)) {
        providerVisibleSpec[key] = IMMUTABLE_AGENT_TASK_PROMPT_SENTINEL;
      }
    } else {
      providerVisibleSpec[key] = value;
    }
  }
  return { providerVisibleSpec, immutableSpec };
}

/** Restore exact immutable fields and discard provider additions or mutations. */
export function restoreScenarioRevisionSpec(
  family: string,
  revisedSpec: Record<string, unknown>,
  immutableSpec: Record<string, unknown>,
): Record<string, unknown> {
  if (family !== "agent_task") return { ...revisedSpec };
  const mutableRevisedSpec = Object.fromEntries(
    Object.entries(revisedSpec).filter(
      ([key]) => !IMMUTABLE_AGENT_TASK_KEYS.has(key),
    ),
  );
  return { ...mutableRevisedSpec, ...immutableSpec };
}
