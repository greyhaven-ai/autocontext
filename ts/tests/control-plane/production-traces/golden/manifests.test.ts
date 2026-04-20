// Golden-manifest byte-equality tests (spec §10.2 row 10, 4 scenarios).
//
// Each scenario runs Layer 5's `buildDataset` with FIXED inputs — pinned
// traceIds, pinned seeds, pinned autoctxVersion, pinned install-salt. The
// resulting `manifest.json` is compared byte-for-byte against the canonical
// file under `./datasets/<scenario>.manifest.json`.
//
// Mismatch is NOT silently overwritten — it fails the test with a diff
// preview. `UPDATE_GOLDEN=1 npx vitest run ...` opts into regeneration.
//
// Scenarios (§10.2 row 10):
//   - single-cluster      — 20 traces all `taskType: checkout`; one
//                           explicit rubric; gate + split (70/15/15);
//                           allowSyntheticRubrics: false.
//   - multi-cluster       — 30 traces across 3 task types; 2 inline rubrics
//                           + 1 rubricLookup (registry) match.
//   - contrastive         — 40 traces, half success / half failure, one
//                           cluster; contrastive rule + split; explicit rubric.
//   - synthetic-rubric    — 15 traces, no explicit / no registry rubric;
//                           allowSyntheticRubrics: true (source="synthetic").
//                           synthetic-rubric requires ≥50% outcome-labeled
//                           traces per spec §8.3 — the fixture carries labels.

import { describe, test, expect } from "vitest";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { buildDataset } from "../../../../src/production-traces/dataset/pipeline.js";
import type {
  BuildDatasetInputs,
  DatasetManifest,
  Rubric,
  SelectionRule,
} from "../../../../src/production-traces/dataset/types.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";
import type { LoadedRedactionPolicy } from "../../../../src/production-traces/redaction/types.js";
import {
  aProductionTrace,
  aMockRubricLookup,
  deterministicTraceId,
} from "../integration/_helpers/fixtures.js";

const UPDATE = process.env.UPDATE_GOLDEN === "1";
const GOLDEN_DIR = resolve(__dirname, "datasets");

// Minimal on-export policy — identical bytes to `dataset/_helpers/fixtures.ts`.
// Copying the bytes keeps dataset-tier tests and golden-tier tests isolated
// but the shape equivalent; divergence here would be a spec-level bug.
const MINIMAL_POLICY: LoadedRedactionPolicy = {
  schemaVersion: "1.0",
  mode: "on-export",
  autoDetect: { enabled: false, categories: [] },
  customPatterns: [],
  rawProviderPayload: { behavior: "blanket-mark" },
  exportPolicy: {
    placeholder: "[redacted]",
    preserveLength: false,
    includeRawProviderPayload: false,
    includeMetadata: true,
    categoryOverrides: {},
  },
};

const DEFAULT_RUBRIC_ACCURACY: Rubric = { rubricId: "rubric-accuracy", dimensions: ["accuracy"] };
const DEFAULT_RUBRIC_SAFETY: Rubric = { rubricId: "rubric-safety", dimensions: ["safety", "harmlessness"] };
const DEFAULT_RUBRIC_LATENCY: Rubric = { rubricId: "rubric-latency", dimensions: ["latency"] };

function runWith(tmp: string, overrides: Partial<BuildDatasetInputs> = {}): BuildDatasetInputs {
  return {
    cwd: tmp,
    name: "golden-dataset",
    description: "golden-manifest fixture",
    traces: [],
    clusterStrategy: "taskType",
    selectionRules: [],
    allowSyntheticRubrics: false,
    redactionPolicy: MINIMAL_POLICY,
    installSalt: null,
    seed: 42,
    autoctxVersion: "layer9-golden",
    ...overrides,
  };
}

function splitRule(seed: number): SelectionRule {
  return {
    type: "split",
    train: 0.7,
    eval: 0.15,
    holdout: 0.15,
    shuffle: false,
    seed,
  };
}

function tracesWithTaskType(count: number, taskType: string, startIndex = 0): ProductionTrace[] {
  const out: ProductionTrace[] = [];
  const anchor = Date.parse("2026-04-17T12:00:00.000Z");
  for (let i = 0; i < count; i++) {
    const startedAt = new Date(anchor + (startIndex + i) * 1000).toISOString();
    out.push(
      aProductionTrace({
        traceId: deterministicTraceId(startIndex + i + 1),
        startedAt,
        taskType,
      }),
    );
  }
  return out;
}

function tracesWithOutcome(
  count: number,
  taskType: string,
  outcomeLabel: "success" | "failure",
  startIndex = 0,
): ProductionTrace[] {
  const out: ProductionTrace[] = [];
  const anchor = Date.parse("2026-04-17T12:00:00.000Z");
  for (let i = 0; i < count; i++) {
    const startedAt = new Date(anchor + (startIndex + i) * 1000).toISOString();
    out.push(
      aProductionTrace({
        traceId: deterministicTraceId(startIndex + i + 1),
        startedAt,
        taskType,
        outcome: {
          label: outcomeLabel,
          score: outcomeLabel === "success" ? 0.95 : 0.1,
        },
      }),
    );
  }
  return out;
}

/**
 * Assert a built dataset's manifest matches the canonical golden file. On
 * `UPDATE_GOLDEN=1`, the golden file is (re-)written from the actual result
 * — tests print a NOTE and PASS.
 *
 * On mismatch (without UPDATE_GOLDEN), we print a compact diff preview so
 * reviewers can decide whether the change is intentional before re-running
 * with UPDATE_GOLDEN=1.
 */
function assertOrUpdateGolden(scenario: string, manifest: DatasetManifest): void {
  // Deterministic serialization — stable JSON with 2-space indent + trailing
  // newline keeps the golden file human-reviewable and diff-friendly.
  const actual = JSON.stringify(manifest, null, 2) + "\n";
  const goldenPath = join(GOLDEN_DIR, `${scenario}.manifest.json`);

  if (UPDATE) {
    writeFileSync(goldenPath, actual, "utf-8");
    // eslint-disable-next-line no-console
    console.log(`[golden] UPDATED ${goldenPath}`);
    return;
  }

  if (!existsSync(goldenPath)) {
    throw new Error(
      `Golden manifest missing: ${goldenPath}. ` +
        `Run with UPDATE_GOLDEN=1 to create it.`,
    );
  }
  const expected = readFileSync(goldenPath, "utf-8");
  if (actual !== expected) {
    const preview = renderDiffPreview(expected, actual, 6);
    throw new Error(
      `Golden manifest mismatch: ${goldenPath}\n` +
        `Diff preview (expected vs actual):\n${preview}\n` +
        `If this change is intentional, re-run with UPDATE_GOLDEN=1 to regenerate.`,
    );
  }
}

function renderDiffPreview(expected: string, actual: string, context: number): string {
  const expLines = expected.split("\n");
  const actLines = actual.split("\n");
  const max = Math.max(expLines.length, actLines.length);
  let firstDiff = -1;
  for (let i = 0; i < max; i++) {
    if ((expLines[i] ?? "") !== (actLines[i] ?? "")) {
      firstDiff = i;
      break;
    }
  }
  if (firstDiff < 0) return "(files equal in content but differ in trailing bytes)";
  const start = Math.max(0, firstDiff - context);
  const end = Math.min(max, firstDiff + context + 1);
  const lines: string[] = [];
  for (let i = start; i < end; i++) {
    const e = expLines[i];
    const a = actLines[i];
    if (e === a) {
      lines.push(`    ${i + 1}: ${e ?? ""}`);
    } else {
      if (e !== undefined) lines.push(`  - ${i + 1}: ${e}`);
      if (a !== undefined) lines.push(`  + ${i + 1}: ${a}`);
    }
  }
  return lines.join("\n");
}

let tmp: string;

describe("Golden manifests (§10.2 row 10, 4 scenarios)", () => {
  // ----------------------------------------------------------------------
  // single-cluster
  // ----------------------------------------------------------------------
  test("single-cluster — 20 traces, taskType=checkout, one explicit rubric, gate + split(70/15/15)", async () => {
    tmp = mkdtempSync(join(tmpdir(), "golden-single-"));
    try {
      const traces = tracesWithTaskType(20, "checkout");
      const inputs = runWith(tmp, {
        name: "single-cluster-dataset",
        description: "single-cluster golden scenario",
        traces,
        clusterStrategy: "taskType",
        selectionRules: [
          { type: "gate", include: [{ "env.taskType": { equals: "checkout" } }] },
          splitRule(7),
        ],
        rubricConfig: {
          rubricsByCluster: {
            checkout: { source: "inline", rubric: DEFAULT_RUBRIC_ACCURACY },
          },
        },
        allowSyntheticRubrics: false,
      });
      const res = await buildDataset(inputs);
      expect(res.stats.traceCount).toBe(20);
      expect(res.stats.clusterCount).toBe(1);
      assertOrUpdateGolden("single-cluster", res.manifest);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  // ----------------------------------------------------------------------
  // multi-cluster
  // ----------------------------------------------------------------------
  test("multi-cluster — 30 traces across 3 task types; 2 inline + 1 registry rubric", async () => {
    tmp = mkdtempSync(join(tmpdir(), "golden-multi-"));
    try {
      // 10 each of checkout (inline), password-reset (inline), support
      // (rubricLookup returns grid_ctf's rubric via scenarioId link).
      const t1 = tracesWithTaskType(10, "checkout", 0);
      const t2 = tracesWithTaskType(10, "password-reset", 10);
      // `support` traces carry scenarioId so the registry lookup gets invoked.
      const t3 = tracesWithTaskType(10, "support", 20).map((t) => ({
        ...t,
        links: { ...t.links, scenarioId: t.links?.scenarioId ?? ("grid_ctf" as never) },
      })) as ProductionTrace[];
      const traces = [...t1, ...t2, ...t3];

      const rubricLookup = aMockRubricLookup({
        grid_ctf: DEFAULT_RUBRIC_LATENCY,
      });

      const inputs = runWith(tmp, {
        name: "multi-cluster-dataset",
        description: "multi-cluster golden scenario",
        traces,
        clusterStrategy: "taskType",
        selectionRules: [splitRule(11)],
        rubricConfig: {
          rubricsByCluster: {
            checkout: { source: "inline", rubric: DEFAULT_RUBRIC_ACCURACY },
            "password-reset": { source: "inline", rubric: DEFAULT_RUBRIC_SAFETY },
          },
        },
        rubricLookup,
        allowSyntheticRubrics: false,
      });

      const res = await buildDataset(inputs);
      expect(res.stats.traceCount).toBe(30);
      assertOrUpdateGolden("multi-cluster", res.manifest);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  // ----------------------------------------------------------------------
  // contrastive
  // ----------------------------------------------------------------------
  test("contrastive — 40 traces (half success / half failure); contrastive + split; one explicit rubric", async () => {
    tmp = mkdtempSync(join(tmpdir(), "golden-contrastive-"));
    try {
      const successes = tracesWithOutcome(20, "support", "success", 0);
      const failures = tracesWithOutcome(20, "support", "failure", 20);
      const traces = [...successes, ...failures];

      const inputs = runWith(tmp, {
        name: "contrastive-dataset",
        description: "contrastive golden scenario",
        traces,
        clusterStrategy: "taskType",
        selectionRules: [
          {
            type: "contrastive",
            failureCriterion: { "outcome.label": { equals: "failure" } },
            successCriterion: { "outcome.label": { equals: "success" } },
            pairStrategy: "same-cluster",
            maxPairsPerCluster: 20,
          },
          splitRule(13),
        ],
        rubricConfig: {
          rubricsByCluster: {
            support: { source: "inline", rubric: DEFAULT_RUBRIC_ACCURACY },
          },
        },
        allowSyntheticRubrics: false,
      });

      const res = await buildDataset(inputs);
      expect(res.stats.traceCount).toBe(40);
      assertOrUpdateGolden("contrastive", res.manifest);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  // ----------------------------------------------------------------------
  // synthetic-rubric
  // ----------------------------------------------------------------------
  test("synthetic-rubric — 15 traces, no explicit / no registry rubric; allowSyntheticRubrics enabled", async () => {
    tmp = mkdtempSync(join(tmpdir(), "golden-synth-"));
    try {
      // Spec §8.3 requires ≥50% of traces to carry an outcome label for
      // synthetic rubric generation. 10 of 15 carry a label (8 success, 2
      // failure) → 66% labeled, well above threshold.
      const labeled = tracesWithOutcome(10, "unknown-task-type", "success", 0);
      // Two of those get overwritten to "failure" so the rubric has mixed labels.
      labeled[8] = {
        ...labeled[8]!,
        outcome: { label: "failure", score: 0.1 },
      };
      labeled[9] = {
        ...labeled[9]!,
        outcome: { label: "failure", score: 0.1 },
      };
      const unlabeled = tracesWithTaskType(5, "unknown-task-type", 10);
      const traces = [...labeled, ...unlabeled];

      const inputs = runWith(tmp, {
        name: "synthetic-rubric-dataset",
        description: "synthetic-rubric golden scenario",
        traces,
        clusterStrategy: "taskType",
        selectionRules: [splitRule(17)],
        // No rubricConfig entries → explicit source exhausted.
        rubricConfig: { rubricsByCluster: {} },
        // aMockRubricLookup() with no overrides → registry always returns null.
        rubricLookup: aMockRubricLookup({}),
        allowSyntheticRubrics: true,
      });

      const res = await buildDataset(inputs);
      expect(res.stats.traceCount).toBe(15);
      // synthetic source is taken — no cluster skipped for rubric absence.
      expect(res.stats.clustersSkipped).toBe(0);
      const clusterEntry = res.manifest.clusters.find(
        (c) => c.clusterId === "unknown-task-type",
      );
      expect(clusterEntry?.rubricSource).toBe("synthetic");
      assertOrUpdateGolden("synthetic-rubric", res.manifest);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});
