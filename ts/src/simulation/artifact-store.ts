import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
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

  const persisted = JSON.parse(readFileSync(specPath, "utf-8")) as Record<
    string,
    unknown
  >;
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
      const report = JSON.parse(
        readFileSync(baseReportPath, "utf-8"),
      ) as SimulationResult;
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
      const report = JSON.parse(
        readFileSync(replayReportPath, "utf-8"),
      ) as SimulationResult;
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
