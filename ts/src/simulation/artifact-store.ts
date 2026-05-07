import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { getScenarioTypeMarker } from "../scenarios/families.js";
import type { ScenarioFamilyName } from "../scenarios/families.js";
import type { SimulationResult } from "./types.js";

export interface ResolvedSimulationArtifact {
  scenarioDir: string;
  reportPath: string;
  report: SimulationResult;
}

export interface PersistSimulationArtifactsOpts {
  knowledgeRoot: string;
  name: string;
  family: ScenarioFamilyName;
  spec: Record<string, unknown>;
  source: string;
  scenarioDir?: string;
}

const JsonObjectSchema = z.object({}).passthrough();

function readJsonObject(path: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, "utf-8"));
    const result = JsonObjectSchema.safeParse(parsed);
    return result.success ? result.data : null;
  } catch {
    return null;
  }
}

export function persistSimulationArtifacts(
  opts: PersistSimulationArtifactsOpts,
): string {
  const scenarioDir =
    opts.scenarioDir ?? join(opts.knowledgeRoot, "_simulations", opts.name);

  if (!existsSync(scenarioDir)) {
    mkdirSync(scenarioDir, { recursive: true });
  }

  writeFileSync(
    join(scenarioDir, "spec.json"),
    JSON.stringify({ name: opts.name, family: opts.family, ...opts.spec }, null, 2),
    "utf-8",
  );
  writeFileSync(join(scenarioDir, "scenario.js"), opts.source, "utf-8");
  writeFileSync(
    join(scenarioDir, "scenario_type.txt"),
    getScenarioTypeMarker(opts.family),
    "utf-8",
  );

  return scenarioDir;
}

export function loadPersistedSimulationSpec(
  specPath: string,
): Record<string, unknown> | null {
  if (!existsSync(specPath)) {
    return null;
  }

  const persisted = readJsonObject(specPath);
  if (!persisted) {
    return null;
  }
  const { name: _name, family: _family, ...spec } = persisted;
  return spec;
}

export function resolveSimulationArtifact(
  knowledgeRoot: string,
  id: string,
): ResolvedSimulationArtifact | null {
  const simulationsRoot = join(knowledgeRoot, "_simulations");
  const baseReportPath = join(simulationsRoot, id, "report.json");
  if (existsSync(baseReportPath)) {
    try {
      const report = readJsonObject(baseReportPath) as SimulationResult | null;
      if (!report) return null;
      return {
        scenarioDir: join(simulationsRoot, id),
        reportPath: baseReportPath,
        report,
      };
    } catch {
      return null;
    }
  }

  if (!existsSync(simulationsRoot)) {
    return null;
  }

  for (const entry of readdirSync(simulationsRoot, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith("_")) {
      continue;
    }
    const replayReportPath = join(
      simulationsRoot,
      entry.name,
      `replay_${id}.json`,
    );
    if (!existsSync(replayReportPath)) {
      continue;
    }
    try {
      const report = readJsonObject(replayReportPath) as SimulationResult | null;
      if (!report) return null;
      return {
        scenarioDir: join(simulationsRoot, entry.name),
        reportPath: replayReportPath,
        report,
      };
    } catch {
      return null;
    }
  }

  return null;
}

export function loadSimulationReport(
  knowledgeRoot: string,
  id: string,
): SimulationResult | null {
  return resolveSimulationArtifact(knowledgeRoot, id)?.report ?? null;
}
