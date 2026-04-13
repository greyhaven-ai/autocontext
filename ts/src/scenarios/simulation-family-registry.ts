import { buildSimulationDerivedFamilyGuardCatalog } from "./simulation-family-guard-builders.js";
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

export const SIMULATION_FAMILY_GUARDS = buildSimulationDerivedFamilyGuardCatalog<
  SimulationInterface,
  NegotiationInterface,
  InvestigationInterface,
  WorkflowInterface,
  SchemaEvolutionInterface,
  ToolFragilityInterface,
  OperatorLoopInterface,
  CoordinationInterface
>();
