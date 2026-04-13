/**
 * Runtime interface contracts for all 11 scenario families (AC-380).
 * Mirrors the Python scenario family ABCs with TypeScript type guards.
 */

export {
  isAgentTask,
  isArtifactEditing,
  isCoordination,
  isGameScenario,
  isInvestigation,
  isNegotiation,
  isOperatorLoop,
  isSchemaEvolution,
  isSimulation,
  isToolFragility,
  isWorkflow,
} from "./family-interface-guards.js";
export { assertFamilyContract, detectFamily } from "./family-interface-runtime.js";
export type {
  AgentTaskInterface,
  ArtifactEditingInterface,
  CoordinationInterface,
  GameScenarioInterface,
  InvestigationInterface,
  NegotiationInterface,
  OperatorLoopInterface,
  ScenarioFamilyName,
  SchemaEvolutionInterface,
  SimulationInterface,
  ToolFragilityInterface,
  WorkflowInterface,
} from "./family-interface-types.js";
