/**
 * Dataset-generation orchestrator (spec §8).
 *
 * End-to-end pipeline:
 *
 *   1. Cluster input traces per `clusterStrategy` → Map<clusterId, traces[]>.
 *   2. For each cluster, resolve a rubric (explicit > registry > synthetic >
 *      skip). Skipped clusters are omitted from the dataset but recorded in
 *      the manifest with a skipReason.
 *   3. Apply selection rules (gate/top-quartile/contrastive) per cluster.
 *      The split rule is extracted and applied after row assembly.
 *   4. Assemble DatasetRow[] — one row per trace, plus pair-rows emitted by
 *      any contrastive rule.
 *   5. Apply redaction (redaction/apply.ts) at the row.inputs.messages
 *      boundary. This is the export boundary.
 *   6. Partition rows into train/eval/holdout per the split rule.
 *   7. Compute configHash + inputTracesHash → derive content-addressed
 *      datasetId (or fresh time-ordered ULID when `newId: true`).
 *   8. Write manifest.json + train/eval/holdout JSONL + cluster-stats.json
 *      + copied rubrics to `.autocontext/datasets/<datasetId>/`.
 *   9. Return the result.
 *
 * Redaction at export boundary: every DatasetRow that embeds trace messages
 * passes through `applyRedactions(trace, policy, salt)` before rows are
 * assembled. `source.redactionApplied` is always `true` on output.
 */
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { ulid } from "ulid";
import { applyRedactions } from "../redaction/apply.js";
import { canonicalJsonStringify } from "../../control-plane/contract/canonical-json.js";
import { deriveDatasetId } from "../contract/content-address.js";
import type { ProductionTrace, ToolCall } from "../contract/types.js";
import type {
  BuildDatasetInputs,
  BuildDatasetResult,
  ClusterConfig,
  DatasetId,
  DatasetManifest,
  DatasetRow,
  DatasetRowSplit,
  ManifestClusterEntry,
  Rubric,
  RubricResolution,
} from "./types.js";
import { parseDatasetId } from "./types.js";
import { clusterByRules, clusterByTaskType } from "./cluster.js";
import {
  applySelectionRulesPerCluster,
  extractSplitRule,
  rulesWithoutSplit,
  type TracePair,
} from "./select.js";
import { resolveRubric } from "./rubric.js";
import { partitionByRule } from "./split.js";
import {
  computeConfigHash,
  computeFileHash,
  computeInputTracesHash,
} from "./provenance.js";
import { buildManifest } from "./manifest.js";
import type { ProductionTraceId } from "../contract/branded-ids.js";

export async function buildDataset(inputs: BuildDatasetInputs): Promise<BuildDatasetResult> {
  // --- 1. Cluster ----------------------------------------------------------
  const clusters = clusterTraces(inputs);

  // --- 2. Resolve rubric per cluster --------------------------------------
  const clusterRubrics = new Map<string, RubricResolution>();
  for (const [clusterId, traces] of clusters) {
    const res = await resolveRubric(
      clusterId,
      traces,
      inputs.rubricConfig,
      inputs.rubricLookup,
      { allowSynthetic: inputs.allowSyntheticRubrics, configBaseDir: inputs.cwd },
    );
    clusterRubrics.set(clusterId, res);
  }

  // --- 3. Apply selection rules (non-split) per cluster --------------------
  const nonSplitRules = rulesWithoutSplit(inputs.selectionRules);
  const perCluster = applySelectionRulesPerCluster(clusters, nonSplitRules, inputs.seed);

  // --- 4. Assemble DatasetRow[] (one row per retained trace, two per pair) -
  const rows: DatasetRow[] = [];
  const skippedClusterEntries: ManifestClusterEntry[] = [];
  const includedClusterEntries: ManifestClusterEntry[] = [];

  for (const [clusterId, selection] of perCluster) {
    const rubricRes = clusterRubrics.get(clusterId);
    if (rubricRes === undefined || rubricRes.source === "skip") {
      skippedClusterEntries.push({
        clusterId,
        size: selection.rows.length,
        skippedReason: rubricRes?.skipReason ?? "rubric resolution missing",
      });
      continue;
    }
    if (selection.rows.length === 0) {
      skippedClusterEntries.push({
        clusterId,
        size: 0,
        skippedReason: "no traces retained after selection",
      });
      continue;
    }

    // 5. Redact at export boundary — each trace is run through apply-at-export
    // before any row-level data is extracted from it.
    const redacted = selection.rows.map((t) =>
      applyRedactions(t, inputs.redactionPolicy, inputs.installSalt),
    );
    const redactedById = new Map<string, ProductionTrace>();
    for (const t of redacted) redactedById.set(t.traceId, t);
    // Map pairs through the redacted-by-id lookup so pair-row construction
    // uses the redacted versions (applyRedactions is pure; pointer identity
    // is lost).
    const redactedPairs: TracePair[] | undefined = selection.pairs?.map(([f, s]) => {
      const fR = redactedById.get(f.traceId) ?? f;
      const sR = redactedById.get(s.traceId) ?? s;
      return [fR, sR] as const;
    });

    const clusterRows = assembleClusterRows(
      clusterId,
      redacted,
      redactedPairs,
      rubricRes.rubric,
      rubricRes.source,
    );
    rows.push(...clusterRows);
    includedClusterEntries.push({
      clusterId,
      size: clusterRows.length,
      rubricId: rubricRes.rubric.rubricId,
      rubricSource: rubricRes.source,
    });
  }

  // --- 6. Partition into train/eval/holdout -------------------------------
  const splitRule = extractSplitRule(inputs.selectionRules) ?? {
    type: "split" as const,
    train: 1.0,
    eval: 0.0,
    holdout: 0.0,
    shuffle: false,
    seed: inputs.seed,
  };
  const partitioned = partitionByRule(rows, {
    ...splitRule,
    seed: splitRule.seed ?? inputs.seed,
  });
  const trainRows = partitioned.train.map((r) => ({ ...r, split: "train" as DatasetRowSplit }));
  const evalRows = partitioned.eval.map((r) => ({ ...r, split: "eval" as DatasetRowSplit }));
  const holdoutRows = partitioned.holdout.map((r) => ({ ...r, split: "holdout" as DatasetRowSplit }));

  // --- 7. Compute hashes + datasetId --------------------------------------
  const configForHash = snapshotConfig(inputs);
  const configHash = computeConfigHash(configForHash);
  const allTraceIds = inputs.traces.map((t) => t.traceId);
  const inputTracesHash = computeInputTracesHash(allTraceIds);
  const datasetId = pickDatasetId(inputs, configHash, inputTracesHash);
  const policySnapshotHash = computeConfigHash(inputs.redactionPolicy);

  // --- 8. Write outputs ---------------------------------------------------
  const writePath = join(inputs.cwd, ".autocontext", "datasets", datasetId);
  mkdirSync(writePath, { recursive: true });

  const trainJsonl = rowsToJsonl(trainRows);
  const evalJsonl = rowsToJsonl(evalRows);
  const holdoutJsonl = rowsToJsonl(holdoutRows);
  writeFileSync(join(writePath, "train.jsonl"), trainJsonl, "utf-8");
  writeFileSync(join(writePath, "eval.jsonl"), evalJsonl, "utf-8");
  writeFileSync(join(writePath, "holdout.jsonl"), holdoutJsonl, "utf-8");

  const splits: DatasetManifest["splits"] = {
    train:   { rowCount: trainRows.length,   fileHash: computeFileHash(trainJsonl) },
    eval:    { rowCount: evalRows.length,    fileHash: computeFileHash(evalJsonl) },
    holdout: { rowCount: holdoutRows.length, fileHash: computeFileHash(holdoutJsonl) },
  };

  // Copy rubrics (one file per distinct rubric; included clusters only).
  const rubricsDir = join(writePath, "rubrics");
  mkdirSync(rubricsDir, { recursive: true });
  const seenRubrics = new Set<string>();
  for (const [, res] of clusterRubrics) {
    if (res.source === "skip") continue;
    if (seenRubrics.has(res.rubric.rubricId)) continue;
    seenRubrics.add(res.rubric.rubricId);
    writeFileSync(
      join(rubricsDir, `${res.rubric.rubricId}.json`),
      canonicalJsonStringify(res.rubric) + "\n",
      "utf-8",
    );
  }

  const timeRange = computeTimeRange(inputs.traces);

  const manifest = buildManifest({
    datasetId,
    name: inputs.name,
    description: inputs.description ?? "",
    createdAt: deriveCreatedAt(inputs),
    autoctxVersion: inputs.autoctxVersion,
    traceCount: inputs.traces.length,
    timeRange,
    clusterStrategy: inputs.clusterStrategy,
    filterRules: inputs.selectionRules,
    redactionPolicy: {
      mode: inputs.redactionPolicy.mode,
      snapshotHash: policySnapshotHash,
    },
    splits,
    clusters: [...includedClusterEntries, ...skippedClusterEntries],
    provenance: { configHash, inputTracesHash },
  });
  // Manifest written last so partial failures don't leave a stale manifest.
  writeFileSync(
    join(writePath, "manifest.json"),
    canonicalJsonStringify(manifest) + "\n",
    "utf-8",
  );
  const clusterStats = {
    clusters: manifest.clusters,
    included: includedClusterEntries.length,
    skipped: skippedClusterEntries.length,
  };
  writeFileSync(
    join(writePath, "cluster-stats.json"),
    canonicalJsonStringify(clusterStats) + "\n",
    "utf-8",
  );

  return {
    datasetId,
    manifest,
    writePath,
    stats: {
      traceCount: inputs.traces.length,
      clusterCount: clusters.size,
      clustersSkipped: skippedClusterEntries.length,
      splitSizes: {
        train: trainRows.length,
        eval: evalRows.length,
        holdout: holdoutRows.length,
      },
    },
  };
}

// ---- Helpers ---------------------------------------------------------------

function clusterTraces(inputs: BuildDatasetInputs): Map<string, ProductionTrace[]> {
  if (inputs.clusterStrategy === "taskType") {
    return clusterByTaskType(inputs.traces);
  }
  // strategy === "rules"
  if (inputs.clusterConfig === undefined) {
    throw new Error("buildDataset: clusterStrategy='rules' requires clusterConfig");
  }
  return clusterByRules(inputs.traces, inputs.clusterConfig as ClusterConfig);
}

function assembleClusterRows(
  clusterId: string,
  redacted: readonly ProductionTrace[],
  pairs: readonly TracePair[] | undefined,
  rubric: Rubric,
  rubricSource: "explicit" | "registry" | "synthetic",
): DatasetRow[] {
  const out: DatasetRow[] = [];
  if (pairs !== undefined) {
    // Pair-mode: emit two rows per pair (failure row first, then success row),
    // sharing source.traceIds for traceability.
    for (const [f, s] of pairs) {
      out.push(toRow(clusterId, [f, s], rubric, rubricSource));
      out.push(toRow(clusterId, [s, f], rubric, rubricSource));
    }
    return out;
  }
  for (const t of redacted) {
    out.push(toRow(clusterId, [t], rubric, rubricSource));
  }
  return out;
}

function toRow(
  clusterId: string,
  traces: readonly ProductionTrace[],
  rubric: Rubric,
  rubricSource: "explicit" | "registry" | "synthetic",
): DatasetRow {
  const primary = traces[0];
  const from = min(traces.map((t) => t.timing.startedAt));
  const to = max(traces.map((t) => t.timing.endedAt));
  const toolsAvailable = uniqueToolNames(primary.toolCalls);
  const traceIds = traces.map((t) => t.traceId) as ProductionTraceId[];
  const rowId = deterministicRowId(traceIds, clusterId);
  const expectedOutcome = primary.outcome !== undefined
    && primary.outcome.label !== undefined
    && primary.outcome.label !== "unknown"
    ? {
        label: primary.outcome.label,
        ...(primary.outcome.score !== undefined ? { score: primary.outcome.score } : {}),
        ...(primary.outcome.reasoning !== undefined ? { reasoning: primary.outcome.reasoning } : {}),
      }
    : undefined;
  const row: DatasetRow = {
    schemaVersion: "1.0",
    rowId,
    split: "train" as DatasetRowSplit, // placeholder; overwritten after partitioning
    clusterId,
    source: {
      traceIds,
      timeRange: { from, to },
      redactionApplied: true,
    },
    inputs: {
      messages: primary.messages,
      toolsAvailable,
    },
    ...(expectedOutcome !== undefined ? { expectedOutcome } : {}),
    rubric: {
      rubricId: rubric.rubricId,
      dimensions: rubric.dimensions,
      source: rubricSource,
    },
    metadata: {},
  };
  return row;
}

function uniqueToolNames(calls: readonly ToolCall[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const c of calls) {
    if (!seen.has(c.toolName)) {
      seen.add(c.toolName);
      out.push(c.toolName);
    }
  }
  return out;
}

function deterministicRowId(traceIds: readonly string[], clusterId: string): string {
  // Crockford-base32 ULID-shaped string derived deterministically from the
  // participating traceIds + clusterId. Stable across re-builds when the
  // constituent traces are identical.
  const input = canonicalJsonStringify({ traceIds: [...traceIds].sort(), clusterId });
  const digest = createHash("sha256").update(input).digest();
  return crockfordBase32Encode(digest).slice(0, 26);
}

const CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
function crockfordBase32Encode(buf: Buffer): string {
  let bits = 0;
  let value = 0;
  let out = "";
  for (const b of buf) {
    value = (value << 8) | b;
    bits += 8;
    while (bits >= 5) {
      bits -= 5;
      const idx = (value >>> bits) & 0x1f;
      out += CROCKFORD_ALPHABET[idx];
    }
  }
  if (bits > 0) {
    const idx = (value << (5 - bits)) & 0x1f;
    out += CROCKFORD_ALPHABET[idx];
  }
  return out;
}

function min(values: readonly string[]): string {
  if (values.length === 0) return new Date(0).toISOString();
  return values.reduce((a, b) => (a < b ? a : b));
}

function max(values: readonly string[]): string {
  if (values.length === 0) return new Date(0).toISOString();
  return values.reduce((a, b) => (a > b ? a : b));
}

function computeTimeRange(traces: readonly ProductionTrace[]): { from: string; to: string } {
  if (traces.length === 0) {
    const t = "1970-01-01T00:00:00.000Z";
    return { from: t, to: t };
  }
  const starts = traces.map((t) => t.timing.startedAt);
  const ends = traces.map((t) => t.timing.endedAt);
  return { from: min(starts), to: max(ends) };
}

function snapshotConfig(inputs: BuildDatasetInputs): unknown {
  // Every knob that affects the output rows/splits. Excludes `cwd` (I/O
  // location doesn't affect content), `traces` (hashed separately as
  // inputTracesHash), `rubricLookup` (function — not hashable), and
  // `autoctxVersion` (the content of a dataset doesn't depend on the
  // generating binary version; we capture autoctxVersion in the manifest
  // as plain metadata).
  return {
    name: inputs.name,
    description: inputs.description ?? "",
    clusterStrategy: inputs.clusterStrategy,
    clusterConfig: inputs.clusterConfig ?? null,
    selectionRules: inputs.selectionRules,
    rubricConfig: inputs.rubricConfig ?? null,
    allowSyntheticRubrics: inputs.allowSyntheticRubrics,
    redactionPolicy: inputs.redactionPolicy,
    seed: inputs.seed,
  };
}

function pickDatasetId(
  inputs: BuildDatasetInputs,
  configHash: ReturnType<typeof computeConfigHash>,
  inputTracesHash: ReturnType<typeof computeInputTracesHash>,
): DatasetId {
  if (inputs.newId === true) {
    const fresh = `ds_${ulid()}`;
    const parsed = parseDatasetId(fresh);
    if (parsed === null) {
      throw new Error(`buildDataset: ulid produced non-matching DatasetId: ${fresh}`);
    }
    return parsed;
  }
  const derived = deriveDatasetId(configHash, inputTracesHash);
  const parsed = parseDatasetId(derived);
  if (parsed === null) {
    throw new Error(`buildDataset: deriveDatasetId produced non-matching DatasetId: ${derived}`);
  }
  return parsed;
}

/**
 * `createdAt` is tricky for idempotence: if we used `new Date().toISOString()`
 * at write time, two runs of the same build would differ in manifest bytes.
 *
 * Solution: derive `createdAt` from the input time range when ID is
 * content-addressed (so it's stable across re-runs), and use `new Date()`
 * only when `newId: true` (explicit opt-in to per-build uniqueness).
 */
function deriveCreatedAt(inputs: BuildDatasetInputs): string {
  if (inputs.newId === true) {
    return new Date().toISOString();
  }
  if (inputs.traces.length === 0) return "1970-01-01T00:00:00.000Z";
  return max(inputs.traces.map((t) => t.timing.endedAt));
}

function rowsToJsonl(rows: readonly DatasetRow[]): string {
  if (rows.length === 0) return "";
  return rows.map((r) => canonicalJsonStringify(r)).join("\n") + "\n";
}
