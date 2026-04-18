import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openRegistry } from "../../../src/control-plane/registry/index.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import { createArtifact, createEvalRun } from "../../../src/control-plane/contract/factories.js";
import { validateEvalRunForIngestion } from "../../../src/control-plane/eval-ingest/validator.js";
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

function setupRegistryWithArtifact(registryRoot: string): Artifact {
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

function makeEvalRun(artifactId: ArtifactId, overrides: Partial<EvalRun> = {}): EvalRun {
  return {
    ...createEvalRun({
      runId: "run_1",
      artifactId,
      suiteId: "prod-eval" as SuiteId,
      metrics: okMetrics,
      datasetProvenance: {
        datasetId: "ds-1",
        sliceHash: ("sha256:" + "a".repeat(64)) as ContentHash,
        sampleCount: 100,
      },
      ingestedAt: "2026-04-17T12:05:00.000Z",
    }),
    ...overrides,
  };
}

describe("validateEvalRunForIngestion", () => {
  let registryRoot: string;
  let artifact: Artifact;

  beforeEach(() => {
    registryRoot = mkdtempSync(join(tmpdir(), "autocontext-eval-ingest-"));
    artifact = setupRegistryWithArtifact(registryRoot);
  });

  afterEach(() => {
    rmSync(registryRoot, { recursive: true, force: true });
  });

  test("accepts a well-formed EvalRun targeting a known artifact", () => {
    const reg = openRegistry(registryRoot);
    const run = makeEvalRun(artifact.id);
    const r = validateEvalRunForIngestion(run, { registry: reg });
    expect(r.valid).toBe(true);
  });

  test("rejects when artifactId does not resolve to a registered artifact", () => {
    const reg = openRegistry(registryRoot);
    const run = makeEvalRun("01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId);
    const r = validateEvalRunForIngestion(run, { registry: reg });
    expect(r.valid).toBe(false);
    if (!r.valid) {
      expect(r.errors.join(" ")).toMatch(/artifact|unknown/i);
    }
  });

  test("rejects empty suiteId", () => {
    const reg = openRegistry(registryRoot);
    // Synthesize an invalid run at the wire layer — bypasses factory type checking.
    const run = { ...makeEvalRun(artifact.id), suiteId: "" as unknown as SuiteId };
    const r = validateEvalRunForIngestion(run, { registry: reg });
    expect(r.valid).toBe(false);
  });

  test("rejects empty runId", () => {
    const reg = openRegistry(registryRoot);
    const run = { ...makeEvalRun(artifact.id), runId: "" };
    const r = validateEvalRunForIngestion(run, { registry: reg });
    expect(r.valid).toBe(false);
    if (!r.valid) {
      expect(r.errors.join(" ")).toMatch(/runId/i);
    }
  });

  test("rejects invalid datasetProvenance.sliceHash", () => {
    const reg = openRegistry(registryRoot);
    const run = makeEvalRun(artifact.id, {
      datasetProvenance: {
        datasetId: "ds-1",
        sliceHash: "not-a-hash" as ContentHash,
        sampleCount: 10,
      },
    });
    const r = validateEvalRunForIngestion(run, { registry: reg });
    expect(r.valid).toBe(false);
    if (!r.valid) {
      expect(r.errors.join(" ")).toMatch(/slice|hash/i);
    }
  });

  test("rejects invalid evalRunnerIdentity.configHash", () => {
    const reg = openRegistry(registryRoot);
    const run = makeEvalRun(artifact.id, {
      metrics: {
        ...okMetrics,
        evalRunnerIdentity: {
          name: "x",
          version: "1",
          configHash: "bogus" as ContentHash,
        },
      },
    });
    const r = validateEvalRunForIngestion(run, { registry: reg });
    expect(r.valid).toBe(false);
    if (!r.valid) {
      expect(r.errors.join(" ")).toMatch(/configHash|hash/i);
    }
  });

  test("rejects NaN in metric fields", () => {
    const reg = openRegistry(registryRoot);
    const run = makeEvalRun(artifact.id, {
      metrics: {
        ...okMetrics,
        quality: { score: Number.NaN, sampleSize: 10 },
      },
    });
    const r = validateEvalRunForIngestion(run, { registry: reg });
    expect(r.valid).toBe(false);
    if (!r.valid) {
      expect(r.errors.join(" ")).toMatch(/finite|NaN/i);
    }
  });

  test("rejects Infinity in cost.tokensIn", () => {
    const reg = openRegistry(registryRoot);
    const run = makeEvalRun(artifact.id, {
      metrics: {
        ...okMetrics,
        cost: { tokensIn: Number.POSITIVE_INFINITY, tokensOut: 10 },
      },
    });
    const r = validateEvalRunForIngestion(run, { registry: reg });
    expect(r.valid).toBe(false);
  });

  test("rejects missing required schema fields (e.g. no safety block)", () => {
    const reg = openRegistry(registryRoot);
    // Synthesize an EvalRun with missing safety.regressions by spreading.
    const badMetrics: unknown = {
      quality: { score: 0.9, sampleSize: 100 },
      cost: { tokensIn: 100, tokensOut: 50 },
      latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
      // safety intentionally missing
      evalRunnerIdentity: {
        name: "x",
        version: "1",
        configHash: "sha256:" + "9".repeat(64),
      },
    };
    const run = { ...makeEvalRun(artifact.id), metrics: badMetrics as MetricBundle };
    const r = validateEvalRunForIngestion(run, { registry: reg });
    expect(r.valid).toBe(false);
  });
});
