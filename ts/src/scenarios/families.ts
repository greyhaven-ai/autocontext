export type ScenarioFamilyName =
  | "game"
  | "agent_task"
  | "simulation"
  | "artifact_editing"
  | "investigation"
  | "workflow";

export const SCENARIO_TYPE_MARKERS: Record<ScenarioFamilyName, string> = {
  game: "parametric",
  agent_task: "agent_task",
  simulation: "simulation",
  artifact_editing: "artifact_editing",
  investigation: "investigation",
  workflow: "workflow",
};

export function getScenarioTypeMarker(family: ScenarioFamilyName): string {
  return SCENARIO_TYPE_MARKERS[family];
}
