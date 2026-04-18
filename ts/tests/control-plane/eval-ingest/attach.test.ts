import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, readFileSync, existsSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openRegistry } from "../../../src/control-plane/registry/index.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import { artifactDirectory } from "../../../src/control-plane/registry/artifact-store.js";
import { createArtifact, createEvalRun } from "../../../src/control-plane/contract/factories.js";
import { attachEvalRun } from "../../../src/control-plane/eval-ingest/attach.js";
import { EvalRunAlreadyAttachedError } from "../../../src/control-plane/eval-ingest/errors.js";
import type {
  ArtifactId,
  ContentHash,
  Scenario,
  SuiteId,
} from "../../../src/control-plane/contract/branded-ids.js";
import type {
  Artifact,
  EvalRun,
  MetricBundle,
  Provenance,
} from "../../../src/control-plane/contract/types.js";

const provenance: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

const okMetrics: MetricBundle = {
  quality: { score: 0.9, sampleSize: 100 },
  cost: { tokensIn: 100, tokensOut: 50 },
  latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
  safety: { regressions: [] },
  evalRunnerIdentity: {
    name: "test-eval",
    version: "1.0.0",
    configHash: ("sha256:" + "9".repeat(64)) as ContentHash,
  },
};

function setupArtifact(registryRoot: string): Artifact {
  const reg = openRegistry(registryRoot);
  const payload = join(registryRoot, "src-" + Math.random().toString(36).slice(2));
  mkdirSync(payload, { recursive: true });
  writeFileSync(join(payload, "f.txt"), "v1");
  const hash = hashDirectory(payload);
  const artifact = createArtifact({
    actuatorType: "prompt-patch",
    scenario: "grid_ctf" as Scenario,
    payloadHash: hash,
    provenance,
  });
  reg.saveArtifact(artifact, payload);
  return artifact;
}

function makeEvalRun(artifactId: ArtifactId, runId = "run_1"): EvalRun {
  return createEvalRun({
    runId,
    artifactId,
    suiteId: "prod-eval" as SuiteId,
    metrics: okMetrics,
    datasetProvenance: {
      datasetId: "ds-1",
      sliceHash: ("sha256:" + "a".repeat(64)) as ContentHash,
      sampleCount: 100,
    },
    ingestedAt: "2026-04-17T12:05:00.000Z",
  });
}

describe("attachEvalRun", () => {
  let registryRoot: string;

  beforeEach(() => {
    registryRoot = mkdtempSync(join(tmpdir(), "autocontext-eval-attach-"));
  });

  afterEach(() => {
    // Restore permissions if a test chmodded anything.
    try {
      chmodSync(registryRoot, 0o755);
    } catch {
      // ignore
    }
    rmSync(registryRoot, { recursive: true, force: true });
  });

  test("valid ingestion round-trip: EvalRun on disk, EvalRunRef on artifact", async () => {
    const artifact = setupArtifact(registryRoot);
    const reg = openRegistry(registryRoot);
    const run = makeEvalRun(artifact.id);

    const result = await attachEvalRun(reg, run);

    // Returned artifact has a new EvalRunRef.
    expect(result.artifact.evalRuns).toHaveLength(1);
    expect(result.artifact.evalRuns[0]!.evalRunId).toBe("run_1");
    expect(result.artifact.evalRuns[0]!.suiteId).toBe("prod-eval");
    expect(result.evalRun).toEqual(run);

    // On disk: EvalRun file exists.
    const runPath = join(artifactDirectory(registryRoot, artifact.id), "eval-runs", "run_1.json");
    expect(existsSync(runPath)).toBe(true);
    const stored = JSON.parse(readFileSync(runPath, "utf-8"));
    expect(stored.runId).toBe("run_1");

    // Re-open registry and confirm evalRunRef persisted on the artifact metadata.
    const reg2 = openRegistry(registryRoot);
    const reloaded = reg2.loadArtifact(artifact.id);
    expect(reloaded.evalRuns).toHaveLength(1);
    expect(reloaded.evalRuns[0]!.evalRunId).toBe("run_1");

    const reloadedRun = reg2.loadEvalRun(artifact.id, "run_1");
    expect(reloadedRun).toEqual(run);
  });

  test("rejects schema failure — missing metrics.safety", async () => {
    const artifact = setupArtifact(registryRoot);
    const reg = openRegistry(registryRoot);
    // Synthesize a malformed run.
    const bad = {
      ...makeEvalRun(artifact.id),
      metrics: {
        ...okMetrics,
        safety: undefined,
      },
    } as unknown as EvalRun;

    await expect(attachEvalRun(reg, bad)).rejects.toThrow(/safety|valid/i);
  });

  test("rejects unknown artifactId", async () => {
    setupArtifact(registryRoot);
    const reg = openRegistry(registryRoot);
    const run = makeEvalRun("01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId);
    await expect(attachEvalRun(reg, run)).rejects.toThrow(/artifact|unknown/i);
  });

  test("duplicate (artifactId, runId) → EvalRunAlreadyAttachedError", async () => {
    const artifact = setupArtifact(registryRoot);
    const reg = openRegistry(registryRoot);
    const run = makeEvalRun(artifact.id, "run_dup");

    await attachEvalRun(reg, run);
    await expect(attachEvalRun(reg, run)).rejects.toBeInstanceOf(EvalRunAlreadyAttachedError);
  });

  test("NaN in metrics is rejected", async () => {
    const artifact = setupArtifact(registryRoot);
    const reg = openRegistry(registryRoot);
    const bad: EvalRun = {
      ...makeEvalRun(artifact.id),
      metrics: { ...okMetrics, quality: { score: Number.NaN, sampleSize: 10 } },
    };
    await expect(attachEvalRun(reg, bad)).rejects.toThrow();
  });

  test("Infinity in cost is rejected", async () => {
    const artifact = setupArtifact(registryRoot);
    const reg = openRegistry(registryRoot);
    const bad: EvalRun = {
      ...makeEvalRun(artifact.id),
      metrics: { ...okMetrics, cost: { tokensIn: Number.POSITIVE_INFINITY, tokensOut: 0 } },
    };
    await expect(attachEvalRun(reg, bad)).rejects.toThrow();
  });

  test("transactional: if registry write fails mid-way (read-only dir), no partial state visible", async () => {
    const artifact = setupArtifact(registryRoot);
    const reg = openRegistry(registryRoot);

    // Make the eval-runs write path fail by making the artifact directory read-only.
    const aDir = artifactDirectory(registryRoot, artifact.id);
    chmodSync(aDir, 0o555);

    const run = makeEvalRun(artifact.id, "run_x");

    let thrown: unknown = null;
    try {
      await attachEvalRun(reg, run);
    } catch (e) {
      thrown = e;
    }
    // Restore before further assertions.
    chmodSync(aDir, 0o755);

    expect(thrown).not.toBeNull();

    // No EvalRun file was persisted.
    const runPath = join(aDir, "eval-runs", "run_x.json");
    expect(existsSync(runPath)).toBe(false);

    // Artifact metadata still has an empty evalRuns list (no partial append).
    const reg2 = openRegistry(registryRoot);
    const reloaded = reg2.loadArtifact(artifact.id);
    expect(reloaded.evalRuns).toHaveLength(0);
  });

  test("two distinct runIds against the same artifact both persist append-only", async () => {
    const artifact = setupArtifact(registryRoot);
    const reg = openRegistry(registryRoot);
    const r1 = makeEvalRun(artifact.id, "run_1");
    const r2 = makeEvalRun(artifact.id, "run_2");

    await attachEvalRun(reg, r1);
    const second = await attachEvalRun(reg, r2);

    expect(second.artifact.evalRuns.map((e) => e.evalRunId)).toEqual(["run_1", "run_2"]);
  });
});
