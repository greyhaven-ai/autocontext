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
            const data = JSON.parse(
              readFileSync(reportPath, "utf-8"),
            ) as Record<string, unknown>;
            const summary = data.summary as Record<string, unknown> | undefined;
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
        return JSON.parse(readFileSync(reportPath, "utf-8")) as Record<
          string,
          unknown
        >;
      } catch {
        return null;
      }
    },

    getDashboardData(name: string): SimulationDashboardData | null {
      const raw = this.getSimulation(name);
      if (!raw) return null;

      const summary = (raw.summary ?? {}) as Record<string, unknown>;
      const sweep = raw.sweep as
        | { results?: Array<Record<string, unknown>> }
        | undefined;

      const sweepChart = sweep?.results?.map((r) => ({
        variables: (r.variables ?? {}) as Record<string, unknown>,
        score: Number(r.score ?? 0),
        reasoning: String(r.reasoning ?? ""),
      }));

      return {
        name: String(raw.name ?? name),
        family: String(raw.family ?? "simulation"),
        status: String(raw.status ?? "unknown"),
        overallScore: Number(summary.score ?? 0),
        reasoning: String(summary.reasoning ?? ""),
        dimensionScores: (summary.dimensionScores ?? {}) as Record<
          string,
          number
        >,
        sensitivityRanking: (summary.mostSensitiveVariables ?? []) as string[],
        bestCase: summary.bestCase as
          | { score: number; variables: Record<string, unknown> }
          | undefined,
        worstCase: summary.worstCase as
          | { score: number; variables: Record<string, unknown> }
          | undefined,
        sweepChart,
        assumptions: (raw.assumptions ?? []) as string[],
        warnings: (raw.warnings ?? []) as string[],
      };
    },
  };
}
