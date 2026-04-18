import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openRegistry } from "../../../src/control-plane/registry/index.js";
import { createArtifact, createPromotionEvent, createEvalRun } from "../../../src/control-plane/contract/factories.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import { readHistory } from "../../../src/control-plane/registry/history-store.js";
import { artifactDirectory } from "../../../src/control-plane/registry/artifact-store.js";
import { readStatePointer } from "../../../src/control-plane/registry/state-pointer.js";
import type { ContentHash, EnvironmentTag, Scenario } from "../../../src/control-plane/contract/branded-ids.js";
import type { Artifact, MetricBundle, Provenance } from "../../../src/control-plane/contract/types.js";

const aProvenance: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

const aMetrics: MetricBundle = {
  quality: { score: 0.9, sampleSize: 100 },
  cost: { tokensIn: 100, tokensOut: 50 },
  latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
  safety: { regressions: [] },
  evalRunnerIdentity: {
    name: "test-eval",
    version: "1.0.0",
    configHash: "sha256:" + "9".repeat(64),
  },
};

function tempPayload(parent: string, content: string): { dir: string; hash: ContentHash } {
  const dir = join(parent, "src-" + Math.random().toString(36).slice(2));
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "f.txt"), content);
  return { dir, hash: hashDirectory(dir) };
}

function makeArtifact(payloadHash: ContentHash, scenario = "grid_ctf"): Artifact {
  return createArtifact({
    actuatorType: "prompt-patch",
    scenario: scenario as Scenario,
    payloadHash,
    provenance: aProvenance,
  });
}

describe("openRegistry — facade", () => {
  let registryRoot: string;

  beforeEach(() => {
    registryRoot = mkdtempSync(join(tmpdir(), "autocontext-registry-"));
  });

  afterEach(() => {
    rmSync(registryRoot, { recursive: true, force: true });
  });

  test("saveArtifact / loadArtifact round-trip via the facade", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const artifact = makeArtifact(hash);
    reg.saveArtifact(artifact, dir);
    expect(reg.loadArtifact(artifact.id)).toEqual(artifact);
  });

  test("listCandidates returns saved artifacts", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const artifact = makeArtifact(hash);
    reg.saveArtifact(artifact, dir);
    const all = reg.listCandidates({});
    expect(all.map((a) => a.id)).toEqual([artifact.id]);
  });

  test("attachEvalRun persists an EvalRun under the artifact dir", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const artifact = makeArtifact(hash);
    reg.saveArtifact(artifact, dir);
    const run = createEvalRun({
      runId: "run_x",
      artifactId: artifact.id,
      suiteId: "prod-eval-v3" as any,
      metrics: aMetrics,
      datasetProvenance: { datasetId: "ds-1", sliceHash: "sha256:" + "1".repeat(64), sampleCount: 100 },
      ingestedAt: "2026-04-17T12:05:00.000Z",
    });
    reg.attachEvalRun(run);
    const back = reg.loadEvalRun(artifact.id, "run_x");
    expect(back).toEqual(run);
  });

  test("appendPromotionEvent updates artifact, history, AND state pointer atomically when reaching active", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const artifact = makeArtifact(hash);
    reg.saveArtifact(artifact, dir);

    // candidate -> active in one shot (allowed by transitions allow-list)
    const promote = createPromotionEvent({
      from: "candidate",
      to: "active",
      reason: "passes-eval",
      timestamp: "2026-04-17T12:30:00.000Z",
    });
    const updated = reg.appendPromotionEvent(artifact.id, promote);

    expect(updated.activationState).toBe("active");
    expect(updated.promotionHistory).toEqual([promote]);

    // History on disk:
    const aDir = artifactDirectory(registryRoot, artifact.id);
    expect(readHistory(join(aDir, "promotion-history.jsonl"))).toEqual([promote]);

    // State pointer flipped to point at us:
    const pointer = readStatePointer(registryRoot, artifact.scenario, artifact.actuatorType, artifact.environmentTag);
    expect(pointer?.artifactId).toBe(artifact.id);

    // The on-disk metadata also reflects the new state:
    const reloaded = reg.loadArtifact(artifact.id);
    expect(reloaded.activationState).toBe("active");
    expect(reloaded.promotionHistory).toHaveLength(1);
  });

  test("appendPromotionEvent that does NOT reach active leaves state pointer untouched", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const artifact = makeArtifact(hash);
    reg.saveArtifact(artifact, dir);

    const toShadow = createPromotionEvent({
      from: "candidate",
      to: "shadow",
      reason: "first-eval",
      timestamp: "2026-04-17T12:30:00.000Z",
    });
    reg.appendPromotionEvent(artifact.id, toShadow);

    const pointer = readStatePointer(registryRoot, artifact.scenario, artifact.actuatorType, artifact.environmentTag);
    expect(pointer).toBeNull();
  });

  test("when a new active artifact is promoted, the previous active is automatically demoted to deprecated", () => {
    const reg = openRegistry(registryRoot);
    // First active artifact:
    const { dir: dirA, hash: hashA } = tempPayload(registryRoot, "vA");
    const a = makeArtifact(hashA);
    reg.saveArtifact(a, dirA);
    reg.appendPromotionEvent(a.id, createPromotionEvent({
      from: "candidate", to: "active", reason: "first", timestamp: "2026-04-17T12:00:00.000Z",
    }));

    // Second artifact, same scenario/actuatorType/environment:
    const { dir: dirB, hash: hashB } = tempPayload(registryRoot, "vB");
    const b = makeArtifact(hashB);
    reg.saveArtifact(b, dirB);
    reg.appendPromotionEvent(b.id, createPromotionEvent({
      from: "candidate", to: "active", reason: "second-better", timestamp: "2026-04-17T12:10:00.000Z",
    }));

    // a should now be deprecated, b should be active.
    const reloadedA = reg.loadArtifact(a.id);
    const reloadedB = reg.loadArtifact(b.id);
    expect(reloadedA.activationState).toBe("deprecated");
    expect(reloadedB.activationState).toBe("active");

    // Pointer flipped.
    const pointer = readStatePointer(registryRoot, a.scenario, a.actuatorType, a.environmentTag);
    expect(pointer?.artifactId).toBe(b.id);
  });

  test("appendPromotionEvent on an unknown artifact id throws", () => {
    const reg = openRegistry(registryRoot);
    expect(() =>
      reg.appendPromotionEvent("01KPEYB3BRQWK2WSHK9E93N6NP" as any, createPromotionEvent({
        from: "candidate", to: "shadow", reason: "x", timestamp: "2026-04-17T12:00:00.000Z",
      })),
    ).toThrow();
  });
});
