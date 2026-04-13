import type { MethodVariant } from "./family-contract-helpers.js";
import {
  COORDINATION_METHOD_VARIANTS,
  INVESTIGATION_METHOD_VARIANTS,
  matchesSimulationFamilyContract,
  NEGOTIATION_METHOD_VARIANTS,
  OPERATOR_LOOP_METHOD_VARIANTS,
  SCHEMA_EVOLUTION_METHOD_VARIANTS,
  TOOL_FRAGILITY_METHOD_VARIANTS,
  WORKFLOW_METHOD_VARIANTS,
} from "./simulation-family-method-catalogs.js";

export function buildSimulationFamilyGuard<T>(
  methodVariants: readonly MethodVariant[] = [],
): (obj: unknown) => obj is T {
  return (obj: unknown): obj is T => matchesSimulationFamilyContract(obj, methodVariants);
}

export interface SimulationDerivedFamilyGuardCatalog<
  TSimulation,
  TNegotiation,
  TInvestigation,
  TWorkflow,
  TSchemaEvolution,
  TToolFragility,
  TOperatorLoop,
  TCoordination,
> {
  simulation: (obj: unknown) => obj is TSimulation;
  negotiation: (obj: unknown) => obj is TNegotiation;
  investigation: (obj: unknown) => obj is TInvestigation;
  workflow: (obj: unknown) => obj is TWorkflow;
  schemaEvolution: (obj: unknown) => obj is TSchemaEvolution;
  toolFragility: (obj: unknown) => obj is TToolFragility;
  operatorLoop: (obj: unknown) => obj is TOperatorLoop;
  coordination: (obj: unknown) => obj is TCoordination;
}

export function buildSimulationDerivedFamilyGuardCatalog<
  TSimulation,
  TNegotiation,
  TInvestigation,
  TWorkflow,
  TSchemaEvolution,
  TToolFragility,
  TOperatorLoop,
  TCoordination,
>(): SimulationDerivedFamilyGuardCatalog<
  TSimulation,
  TNegotiation,
  TInvestigation,
  TWorkflow,
  TSchemaEvolution,
  TToolFragility,
  TOperatorLoop,
  TCoordination
> {
  return {
    simulation: buildSimulationFamilyGuard<TSimulation>(),
    negotiation: buildSimulationFamilyGuard<TNegotiation>(NEGOTIATION_METHOD_VARIANTS),
    investigation: buildSimulationFamilyGuard<TInvestigation>(INVESTIGATION_METHOD_VARIANTS),
    workflow: buildSimulationFamilyGuard<TWorkflow>(WORKFLOW_METHOD_VARIANTS),
    schemaEvolution: buildSimulationFamilyGuard<TSchemaEvolution>(SCHEMA_EVOLUTION_METHOD_VARIANTS),
    toolFragility: buildSimulationFamilyGuard<TToolFragility>(TOOL_FRAGILITY_METHOD_VARIANTS),
    operatorLoop: buildSimulationFamilyGuard<TOperatorLoop>(OPERATOR_LOOP_METHOD_VARIANTS),
    coordination: buildSimulationFamilyGuard<TCoordination>(COORDINATION_METHOD_VARIANTS),
  };
}
