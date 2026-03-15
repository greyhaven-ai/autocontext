export type ScenarioFamilyName =
  | "game"
  | "agent_task"
  | "simulation"
  | "artifact_editing"
  | "investigation"
  | "workflow"
  | "schema_evolution"
  | "tool_fragility";

export const SCENARIO_TYPE_MARKERS: Record<ScenarioFamilyName, string> = {
  game: "parametric",
  agent_task: "agent_task",
  simulation: "simulation",
  artifact_editing: "artifact_editing",
  investigation: "investigation",
  workflow: "workflow",
  schema_evolution: "schema_evolution",
  tool_fragility: "tool_fragility",
};

export function getScenarioTypeMarker(family: ScenarioFamilyName): string {
  return SCENARIO_TYPE_MARKERS[family];
}
