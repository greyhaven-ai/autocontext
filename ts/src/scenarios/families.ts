export type ScenarioFamilyName =
  | "game"
  | "agent_task"
  | "simulation"
  | "artifact_editing"
  | "investigation"
  | "workflow"
  | "schema_evolution"
  | "tool_fragility"
  | "negotiation"
  | "operator_loop"
  | "coordination";

export const SCENARIO_TYPE_MARKERS: Record<ScenarioFamilyName, string> = {
  game: "parametric",
  agent_task: "agent_task",
  simulation: "simulation",
  artifact_editing: "artifact_editing",
  investigation: "investigation",
  workflow: "workflow",
  schema_evolution: "schema_evolution",
  tool_fragility: "tool_fragility",
  negotiation: "negotiation",
  operator_loop: "operator_loop",
  coordination: "coordination",
};

/**
 * Families that use action-based simulation execution (AC-531).
 *
 * These families generate runtimes with getAvailableActions/executeAction/isTerminal/getResult.
 * Excludes game (no codegen), agent_task (judge-based), and artifact_editing (edit-based).
 */
export const SIMULATION_LIKE_FAMILIES: ReadonlySet<string> =
  new Set<ScenarioFamilyName>([
    "simulation",
    "investigation",
    "workflow",
    "negotiation",
    "schema_evolution",
    "tool_fragility",
    "operator_loop",
    "coordination",
  ]);

export function getScenarioTypeMarker(family: ScenarioFamilyName): string {
  return SCENARIO_TYPE_MARKERS[family];
}
