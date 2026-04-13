import type { ScenarioFamilyName } from "./families.js";

export type FamilyGuard = (obj: unknown) => boolean;

export function buildFamilyGuardCatalog(opts: {
  isGameScenario: FamilyGuard;
  isAgentTask: FamilyGuard;
  isSimulation: FamilyGuard;
  isNegotiation: FamilyGuard;
  isInvestigation: FamilyGuard;
  isWorkflow: FamilyGuard;
  isSchemaEvolution: FamilyGuard;
  isToolFragility: FamilyGuard;
  isOperatorLoop: FamilyGuard;
  isCoordination: FamilyGuard;
  isArtifactEditing: FamilyGuard;
}): Record<ScenarioFamilyName, FamilyGuard> {
  return {
    game: opts.isGameScenario,
    agent_task: opts.isAgentTask,
    simulation: opts.isSimulation,
    negotiation: opts.isNegotiation,
    investigation: opts.isInvestigation,
    workflow: opts.isWorkflow,
    schema_evolution: opts.isSchemaEvolution,
    tool_fragility: opts.isToolFragility,
    operator_loop: opts.isOperatorLoop,
    coordination: opts.isCoordination,
    artifact_editing: opts.isArtifactEditing,
  };
}

export function detectFamilyByCatalog(
  obj: unknown,
  orderedDetectors: Array<readonly [ScenarioFamilyName, FamilyGuard]>,
): ScenarioFamilyName | null {
  for (const [family, guard] of orderedDetectors) {
    if (guard(obj)) {
      return family;
    }
  }
  return null;
}
