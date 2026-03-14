export type ScenarioFamilyName = "agent_task" | "simulation";

export const SCENARIO_TYPE_MARKERS: Record<ScenarioFamilyName, string> = {
  agent_task: "agent_task",
  simulation: "simulation",
};

export function getScenarioTypeMarker(family: ScenarioFamilyName): string {
  return SCENARIO_TYPE_MARKERS[family];
}
