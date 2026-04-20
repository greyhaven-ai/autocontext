/**
 * Rubric precedence resolver for dataset generation (spec §8.3).
 *
 * Precedence order:
 *   1. `explicit`   — per-cluster override via `rubric-config.json`.
 *                     `source: "file"` loads rubric JSON from disk;
 *                     `source: "inline"` uses the embedded object directly.
 *   2. `registry`   — if any trace in the cluster has `links.scenarioId`, the
 *                     injected `rubricLookup` is called with the first such
 *                     scenarioId. The first non-null lookup result wins.
 *   3. `synthetic`  — opt-in only (`allowSynthetic: true`). Synthesizes a
 *                     minimal rubric from `outcome.label` distribution across
 *                     the cluster. Requires ≥50% of traces to carry a label.
 *   4. `skip`       — otherwise; cluster excluded from dataset.
 *
 * `rubricLookup` is DEPENDENCY-INJECTED — Layer 5 does NOT import from
 * `control-plane/registry/`. The CLI layer (Layer 7) wires the real
 * registry-backed lookup; tests use a mock.
 */
import { readFile } from "node:fs/promises";
import { resolve, isAbsolute } from "node:path";
import type { ProductionTrace } from "../contract/types.js";
import type {
  Rubric,
  RubricConfig,
  RubricLookup,
  RubricResolution,
} from "./types.js";

export interface ResolveRubricOptions {
  readonly allowSynthetic: boolean;
  /** Directory against which `source: "file"` paths resolve (defaults to process.cwd()). */
  readonly configBaseDir?: string;
}

export async function resolveRubric(
  clusterId: string,
  clusterTraces: readonly ProductionTrace[],
  config: RubricConfig | undefined,
  rubricLookup: RubricLookup | undefined,
  options: ResolveRubricOptions,
): Promise<RubricResolution> {
  // Source 1: explicit config entry for this cluster.
  const explicit = config?.rubricsByCluster[clusterId];
  if (explicit !== undefined) {
    try {
      const rubric = await loadExplicitRubric(explicit, options.configBaseDir);
      return { source: "explicit", rubric };
    } catch (err) {
      return {
        source: "skip",
        skipReason: `explicit rubric load failed: ${errorMsg(err)}`,
      };
    }
  }

  // Source 2: registry lookup by scenarioId.
  if (rubricLookup !== undefined) {
    for (const trace of clusterTraces) {
      const scenarioId = trace.links?.scenarioId;
      if (scenarioId === undefined) continue;
      const rubric = await rubricLookup(scenarioId);
      if (rubric !== null) return { source: "registry", rubric };
    }
  }

  // Source 3: synthetic (opt-in only).
  if (options.allowSynthetic) {
    const synth = synthesizeRubric(clusterId, clusterTraces);
    if (synth !== null) return { source: "synthetic", rubric: synth };
  }

  // Source 4: skip.
  return {
    source: "skip",
    skipReason: options.allowSynthetic
      ? "no explicit / registry rubric; synthetic requires ≥50% labeled outcomes"
      : "no rubric available; synthetic generation disabled",
  };
}

async function loadExplicitRubric(
  entry: RubricConfig["rubricsByCluster"][string],
  configBaseDir: string | undefined,
): Promise<Rubric> {
  if (entry.source === "inline") return entry.rubric;
  // source: "file"
  const base = configBaseDir ?? process.cwd();
  const path = isAbsolute(entry.path) ? entry.path : resolve(base, entry.path);
  const raw = await readFile(path, "utf-8");
  const parsed = JSON.parse(raw) as unknown;
  if (!isRubricShape(parsed)) {
    throw new Error(`rubric file ${path} does not match Rubric shape`);
  }
  return parsed;
}

function isRubricShape(v: unknown): v is Rubric {
  if (v === null || typeof v !== "object") return false;
  const r = v as Record<string, unknown>;
  if (typeof r.rubricId !== "string" || r.rubricId.length === 0) return false;
  if (!Array.isArray(r.dimensions)) return false;
  return r.dimensions.every((d) => typeof d === "string" && d.length > 0);
}

/**
 * Synthesize a minimal rubric from the cluster's outcome label distribution.
 * Returns null if fewer than 50% of traces have an outcome label.
 *
 * The synthesized rubric has a single dimension `"label_match"` — good enough
 * for first-pass eval of label-based success signals. Callers are expected
 * to overwrite with a real rubric for meaningful evaluation.
 */
function synthesizeRubric(
  clusterId: string,
  traces: readonly ProductionTrace[],
): Rubric | null {
  if (traces.length === 0) return null;
  const labeled = traces.filter((t) => t.outcome?.label !== undefined);
  if (labeled.length * 2 < traces.length) return null; // <50% labeled
  // Deterministic rubricId derived from clusterId so regeneration is stable.
  return {
    rubricId: `synthetic-${clusterId}`,
    dimensions: ["label_match"],
    description: `Auto-synthesized from label distribution across ${labeled.length}/${traces.length} traces.`,
  };
}

function errorMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
