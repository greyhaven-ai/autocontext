import type {
  CoordinationInterface,
  InvestigationInterface,
  NegotiationInterface,
  OperatorLoopInterface,
  SchemaEvolutionInterface,
  SimulationInterface,
  ToolFragilityInterface,
  WorkflowInterface,
} from "./simulation-family-interface-types.js";
import { SIMULATION_FAMILY_GUARDS } from "./simulation-family-registry.js";

export type {
  CoordinationInterface,
  InvestigationInterface,
  NegotiationInterface,
  OperatorLoopInterface,
  SchemaEvolutionInterface,
  SimulationInterface,
  ToolFragilityInterface,
  WorkflowInterface,
};

export const isSimulation = SIMULATION_FAMILY_GUARDS.simulation;
export const isNegotiation = SIMULATION_FAMILY_GUARDS.negotiation;
export const isInvestigation = SIMULATION_FAMILY_GUARDS.investigation;
export const isWorkflow = SIMULATION_FAMILY_GUARDS.workflow;
export const isSchemaEvolution = SIMULATION_FAMILY_GUARDS.schemaEvolution;
export const isToolFragility = SIMULATION_FAMILY_GUARDS.toolFragility;
export const isOperatorLoop = SIMULATION_FAMILY_GUARDS.operatorLoop;
export const isCoordination = SIMULATION_FAMILY_GUARDS.coordination;
