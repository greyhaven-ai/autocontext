import type { FamilyGuard, OrderedFamilyDetector } from "./family-assertion-workflow.js";
import { buildFamilyGuardCatalog } from "./family-detection-catalog.js";
import type { ScenarioFamilyName } from "./families.js";

export interface FamilyInterfaceGuardOptions {
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
}

export function buildFamilyInterfaceGuardCatalog(
  opts: FamilyInterfaceGuardOptions,
): Record<ScenarioFamilyName, FamilyGuard> {
  return buildFamilyGuardCatalog(opts);
}

export function buildFamilyInterfaceDetectorOrder(
  opts: FamilyInterfaceGuardOptions,
): readonly OrderedFamilyDetector[] {
  return [
    ["game", opts.isGameScenario],
    ["artifact_editing", opts.isArtifactEditing],
    ["negotiation", opts.isNegotiation],
    ["investigation", opts.isInvestigation],
    ["workflow", opts.isWorkflow],
    ["schema_evolution", opts.isSchemaEvolution],
    ["tool_fragility", opts.isToolFragility],
    ["operator_loop", opts.isOperatorLoop],
    ["coordination", opts.isCoordination],
    ["simulation", opts.isSimulation],
    ["agent_task", opts.isAgentTask],
  ] as const;
}
