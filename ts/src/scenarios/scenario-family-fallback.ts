const CORE_SCENARIO_FIELDS = new Set(["taskPrompt", "rubric", "description"]);

export function countScenarioFamilySpecificFields(specFields: Record<string, unknown>): number {
  return Object.keys(specFields).filter((key) => !CORE_SCENARIO_FIELDS.has(key)).length;
}

function familySpecificFieldNames(specFields: Record<string, unknown>): string[] {
  return Object.keys(specFields).filter((key) => !CORE_SCENARIO_FIELDS.has(key));
}

export function fallbackCodegenFamilyToAgentTask(
  family: string,
  specFields: Record<string, unknown>,
): string {
  if (family === "agent_task" || family === "game") {
    return family;
  }

  const familySpecificFields = familySpecificFieldNames(specFields);
  if (familySpecificFields.length === 0) {
    return "agent_task";
  }

  const actions = specFields.actions;
  if (
    familySpecificFields.length === 1 &&
    familySpecificFields[0] === "actions" &&
    Array.isArray(actions) &&
    actions.length === 0
  ) {
    return "agent_task";
  }

  return family;
}
