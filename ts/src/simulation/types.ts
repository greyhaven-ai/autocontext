import type { ScenarioFamilyName } from "../scenarios/families.js";
import type { SweepDimension } from "./sweep-dsl.js";

export interface SimulationRequest {
  description: string;
  variables?: Record<string, unknown>;
  sweep?: SweepDimension[];
  runs?: number;
  maxSteps?: number;
  saveAs?: string;
}

export interface SweepResult {
  dimensions: SweepDimension[];
  runs: number;
  results: Array<{
    variables: Record<string, unknown>;
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
  }>;
}

export interface SimulationSummary {
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
  bestCase?: { score: number; variables: Record<string, unknown> };
  worstCase?: { score: number; variables: Record<string, unknown> };
  mostSensitiveVariables?: string[];
}

export interface SimulationExecutionConfig {
  runs: number;
  maxSteps?: number;
  sweep?: SweepDimension[];
}

export type SimulationStatus = "completed" | "degraded" | "failed";

export interface SimulationResult {
  id: string;
  name: string;
  family: ScenarioFamilyName;
  status: SimulationStatus;
  description: string;
  assumptions: string[];
  variables: Record<string, unknown>;
  sweep?: SweepResult;
  summary: SimulationSummary;
  execution?: SimulationExecutionConfig;
  artifacts: {
    scenarioDir: string;
    reportPath?: string;
  };
  warnings: string[];
  error?: string;
  replayOf?: string;
  originalScore?: number;
  scoreDelta?: number;
}

export interface ReplayRequest {
  id: string;
  variables?: Record<string, unknown>;
  maxSteps?: number;
}

export interface CompareRequest {
  left: string;
  right: string;
}

export interface VariableDelta {
  left: unknown;
  right: unknown;
  delta?: number;
}

export interface SimulationCompareResult {
  status: SimulationStatus;
  left: { name: string; score: number; variables: Record<string, unknown> };
  right: { name: string; score: number; variables: Record<string, unknown> };
  scoreDelta: number;
  variableDeltas: Record<string, VariableDelta>;
  dimensionDeltas: Record<string, { left: number; right: number; delta: number }>;
  likelyDrivers: string[];
  summary: string;
  reportPath?: string;
  error?: string;
}
