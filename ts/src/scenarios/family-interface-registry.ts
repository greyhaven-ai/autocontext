import {
  buildFamilyInterfaceDetectorOrder,
  buildFamilyInterfaceGuardCatalog,
} from "./family-interface-catalogs.js";
import {
  isAgentTask,
  isArtifactEditing,
  isGameScenario,
} from "./primary-family-contracts.js";
import {
  isCoordination,
  isInvestigation,
  isNegotiation,
  isOperatorLoop,
  isSchemaEvolution,
  isSimulation,
  isToolFragility,
  isWorkflow,
} from "./simulation-family-contracts.js";

export const FAMILY_INTERFACE_GUARDS = {
  isGameScenario,
  isAgentTask,
  isSimulation,
  isNegotiation,
  isInvestigation,
  isWorkflow,
  isSchemaEvolution,
  isToolFragility,
  isOperatorLoop,
  isCoordination,
  isArtifactEditing,
};

export const FAMILY_INTERFACE_GUARD_CATALOG = buildFamilyInterfaceGuardCatalog(
  FAMILY_INTERFACE_GUARDS,
);

export const FAMILY_INTERFACE_DETECTOR_ORDER = buildFamilyInterfaceDetectorOrder(
  FAMILY_INTERFACE_GUARDS,
);
