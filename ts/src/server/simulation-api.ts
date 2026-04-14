/**
 * Simulation dashboard API routes (AC-449).
 *
 * Reads persisted simulation report.json files from the knowledge
 * directory and transforms them into visualization-ready structures.
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

export interface SimulationListEntry {
  name: string;
  family: string;
  status: string;
  score: number;
}

export interface SweepChartPoint {
  variables: Record<string, unknown>;
  score: number;
  reasoning: string;
}

export interface SimulationDashboardData {
  name: string;
  family: string;
  status: string;
  overallScore: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
  sensitivityRanking: string[];
  bestCase?: { score: number; variables: Record<string, unknown> };
  worstCase?: { score: number; variables: Record<string, unknown> };
  sweepChart?: SweepChartPoint[];
  assumptions: string[];
  warnings: string[];
}

export interface SimulationApiRoutes {
  listSimulations(): SimulationListEntry[];
  getSimulation(name: string): Record<string, unknown> | null;
  getDashboardData(name: string): SimulationDashboardData | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readJsonRecord(path: string): Record<string, unknown> | null {
  const parsed: unknown = JSON.parse(readFileSync(path, "utf-8"));
  return isRecord(parsed) ? parsed : null;
}

function toRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function toNumberRecord(value: unknown): Record<string, number> {
  const output: Record<string, number> = {};
  if (!isRecord(value)) {
    return output;
  }
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === "number") {
      output[key] = entry;
    }
  }
  return output;
}

function toStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === "string")
    : [];
}

function toSimulationCase(
  value: unknown,
): { score: number; variables: Record<string, unknown> } | undefined {
  if (!isRecord(value) || typeof value.score !== "number") {
    return undefined;
  }
  return {
    score: value.score,
    variables: toRecord(value.variables),
  };
}

function toSweepResults(value: unknown): Array<Record<string, unknown>> | undefined {
  if (!isRecord(value) || !Array.isArray(value.results)) {
    return undefined;
  }
  return value.results.filter(isRecord);
}

export function buildSimulationApiRoutes(
  knowledgeRoot: string,
): SimulationApiRoutes {
  const simDir = join(knowledgeRoot, "_simulations");

  function resolveSimulationReportPath(name: string): string | null {
    const normalized = name.trim();
    if (!normalized) return null;
    const simulationDir = resolve(simDir, normalized);
    const rel = relative(simDir, simulationDir);
    if (
      rel === "" ||
      rel === "." ||
      rel.startsWith("..") ||
      rel.includes(".." + "/") ||
      rel.includes(".." + "\\")
    ) {
      return null;
    }
    return join(simulationDir, "report.json");
  }

  return {
    listSimulations(): SimulationListEntry[] {
      if (!existsSync(simDir)) return [];
      const entries: SimulationListEntry[] = [];
      try {
        for (const name of readdirSync(simDir).sort()) {
          const dir = join(simDir, name);
          if (!statSync(dir).isDirectory()) continue;
          const reportPath = join(dir, "report.json");
          if (!existsSync(reportPath)) continue;
          try {
            const data = readJsonRecord(reportPath);
            if (!data) continue;
            const summary = toRecord(data.summary);
            entries.push({
              name: String(data.name ?? name),
              family: String(data.family ?? "simulation"),
              status: String(data.status ?? "unknown"),
              score: Number(summary?.score ?? 0),
            });
          } catch {
            /* skip malformed */
          }
        }
      } catch {
        /* skip */
      }
      return entries;
    },

    getSimulation(name: string): Record<string, unknown> | null {
      const reportPath = resolveSimulationReportPath(name);
      if (!reportPath) return null;
      if (!existsSync(reportPath)) return null;
      try {
        return readJsonRecord(reportPath);
      } catch {
        return null;
      }
    },

    getDashboardData(name: string): SimulationDashboardData | null {
      const raw = this.getSimulation(name);
      if (!raw) return null;

      const summary = toRecord(raw.summary);
      const sweepResults = toSweepResults(raw.sweep);

      const sweepChart = sweepResults?.map((r) => ({
        variables: toRecord(r.variables),
        score: Number(r.score ?? 0),
        reasoning: String(r.reasoning ?? ""),
      }));

      return {
        name: String(raw.name ?? name),
        family: String(raw.family ?? "simulation"),
        status: String(raw.status ?? "unknown"),
        overallScore: Number(summary.score ?? 0),
        reasoning: String(summary.reasoning ?? ""),
        dimensionScores: toNumberRecord(summary.dimensionScores),
        sensitivityRanking: toStringArray(summary.mostSensitiveVariables),
        bestCase: toSimulationCase(summary.bestCase),
        worstCase: toSimulationCase(summary.worstCase),
        sweepChart,
        assumptions: toStringArray(raw.assumptions),
        warnings: toStringArray(raw.warnings),
      };
    },
  };
}
