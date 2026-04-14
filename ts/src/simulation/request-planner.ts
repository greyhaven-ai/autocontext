import { SIMULATION_LIKE_FAMILIES, type ScenarioFamilyName } from "../scenarios/families.js";
import { detectScenarioFamily } from "../scenarios/scenario-creator.js";
import type {
  SimulationExecutionConfig,
  SimulationRequest,
  SimulationResult,
} from "./types.js";

export function deriveSimulationName(description: string): string {
  return (
    description
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, "")
      .split(/\s+/)
      .filter((word) => word.length > 2)
      .slice(0, 4)
      .join("_") || "simulation"
  );
}

export function inferSimulationFamily(description: string): ScenarioFamilyName {
  const family = detectScenarioFamily(description);
  if (SIMULATION_LIKE_FAMILIES.has(family)) {
    return family;
  }
  return "simulation";
}

export function buildSimulationExecutionConfig(
  request: SimulationRequest,
): SimulationExecutionConfig {
  return {
    runs: Math.max(1, request.runs ?? 1),
    maxSteps: request.maxSteps,
    sweep:
      request.sweep && request.sweep.length > 0 ? request.sweep : undefined,
  };
}

export function resolveSimulationExecutionConfig(
  report: SimulationResult,
): SimulationExecutionConfig {
  if (report.execution) {
    return {
      runs: Math.max(1, report.execution.runs ?? 1),
      maxSteps: report.execution.maxSteps,
      sweep:
        report.execution.sweep && report.execution.sweep.length > 0
          ? report.execution.sweep
          : undefined,
    };
  }

  if (report.sweep && report.sweep.results.length > 0) {
    const runsPerCell = Math.max(
      1,
      Math.round(report.sweep.runs / Math.max(report.sweep.results.length, 1)),
    );
    return {
      runs: runsPerCell,
      sweep: report.sweep.dimensions,
    };
  }

  return { runs: 1 };
}

export function collectReplayVariables(
  originalReport: SimulationResult,
  overrides?: Record<string, unknown>,
): Record<string, unknown> {
  return {
    ...(originalReport.variables ?? {}),
    ...(overrides ?? {}),
  };
}
