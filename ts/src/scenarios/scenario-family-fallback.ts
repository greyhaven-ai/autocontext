const CORE_SCENARIO_FIELDS = new Set(["taskPrompt", "rubric", "description"]);

export function countScenarioFamilySpecificFields(specFields: Record<string, unknown>): number {
  return Object.keys(specFields).filter((key) => !CORE_SCENARIO_FIELDS.has(key)).length;
}

export function fallbackCodegenFamilyToAgentTask(
  family: string,
  specFields: Record<string, unknown>,
): string {
  if (family === "agent_task" || family === "game") {
    return family;
  }

  if (countScenarioFamilySpecificFields(specFields) === 0) {
    return "agent_task";
  }

  const actions = specFields.actions;
  if (Array.isArray(actions) && actions.length === 0) {
    return "agent_task";
  }

  return family;
}
