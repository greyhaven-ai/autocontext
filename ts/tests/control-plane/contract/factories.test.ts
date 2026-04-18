import { describe, test, expect } from "vitest";
import {
  createArtifact,
  createPromotionEvent,
  createEvalRun,
} from "../../../src/control-plane/contract/factories.js";
import { appendPromotionEvent } from "../../../src/control-plane/promotion/append.js";
import {
  validateArtifact,
  validatePromotionEvent,
  validateEvalRun,
} from "../../../src/control-plane/contract/validators.js";
import type { Provenance, MetricBundle } from "../../../src/control-plane/contract/types.js";

const aProvenance: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

const aMetricBundle: MetricBundle = {
  quality: { score: 0.8, sampleSize: 100 },
  cost: { tokensIn: 1000, tokensOut: 500 },
  latency: { p50Ms: 100, p95Ms: 200, p99Ms: 300 },
  safety: { regressions: [] },
  evalRunnerIdentity: {
    name: "my-eval",
    version: "1.0.0",
    configHash: "sha256:" + "a".repeat(64),
  },
};

describe("createArtifact", () => {
  test("produces a valid Artifact in candidate state with fresh ULID and defaults", () => {
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: "sha256:" + "b".repeat(64),
      provenance: aProvenance,
    });
    expect(artifact.actuatorType).toBe("prompt-patch");
    expect(artifact.scenario).toBe("grid_ctf");
    expect(artifact.environmentTag).toBe("production");
    expect(artifact.activationState).toBe("candidate");
    expect(artifact.promotionHistory).toEqual([]);
    expect(artifact.evalRuns).toEqual([]);
    expect(artifact.id).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/);
    expect(artifact.schemaVersion).toBe("1.0");
    expect(validateArtifact(artifact).valid).toBe(true);
  });

  test("respects overrides for id and environmentTag (for tests / legacy adapter)", () => {
    const artifact = createArtifact({
      actuatorType: "tool-policy",
      scenario: "othello",
      environmentTag: "staging",
      payloadHash: "sha256:" + "c".repeat(64),
      provenance: aProvenance,
      id: "01KPEYB3BQNFDEYRS8KH538PF5",
    });
    expect(artifact.id).toBe("01KPEYB3BQNFDEYRS8KH538PF5");
    expect(artifact.environmentTag).toBe("staging");
    expect(validateArtifact(artifact).valid).toBe(true);
  });

  test("different invocations produce different ULIDs (time-ordered)", () => {
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: "sha256:" + "d".repeat(64),
      provenance: aProvenance,
    });
    const b = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: "sha256:" + "d".repeat(64),
      provenance: aProvenance,
    });
    expect(a.id).not.toBe(b.id);
  });
});

describe("createPromotionEvent", () => {
  test("produces a valid event with provided fields", () => {
    const event = createPromotionEvent({
      from: "candidate",
      to: "shadow",
      reason: "first eval",
      timestamp: "2026-04-17T12:10:00.000Z",
    });
    expect(event.from).toBe("candidate");
    expect(event.to).toBe("shadow");
    expect(event.reason).toBe("first eval");
    expect(event.timestamp).toBe("2026-04-17T12:10:00.000Z");
    expect(validatePromotionEvent(event).valid).toBe(true);
  });

  test("preserves optional evidence and signature", () => {
    const event = createPromotionEvent({
      from: "shadow",
      to: "canary",
      reason: "passed shadow",
      timestamp: "2026-04-17T13:00:00.000Z",
      evidence: { suiteId: "prod-eval-v3" },
      signature: "sig-abc",
    });
    expect(event.evidence).toEqual({ suiteId: "prod-eval-v3" });
    expect(event.signature).toBe("sig-abc");
    expect(validatePromotionEvent(event).valid).toBe(true);
  });
});

describe("createEvalRun", () => {
  test("produces a valid EvalRun", () => {
    const run = createEvalRun({
      runId: "eval_123",
      artifactId: "01KPEYB3BRQWK2WSHK9E93N6NP",
      suiteId: "prod-eval-v3",
      metrics: aMetricBundle,
      datasetProvenance: {
        datasetId: "prod-traces-2026-04-15",
        sliceHash: "sha256:" + "e".repeat(64),
        sampleCount: 300,
      },
      ingestedAt: "2026-04-17T12:05:00.000Z",
    });
    expect(run.schemaVersion).toBe("1.0");
    expect(validateEvalRun(run).valid).toBe(true);
  });
});

describe("appendPromotionEvent (immutable, state-transition enforcing)", () => {
  test("returns a new Artifact with the event appended and activationState updated", () => {
    const before = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: "sha256:" + "f".repeat(64),
      provenance: aProvenance,
    });
    const event = createPromotionEvent({
      from: "candidate",
      to: "shadow",
      reason: "first eval",
      timestamp: "2026-04-17T12:10:00.000Z",
    });
    const after = appendPromotionEvent(before, event);
    expect(after.activationState).toBe("shadow");
    expect(after.promotionHistory).toHaveLength(1);
    expect(after.promotionHistory[0]).toEqual(event);
    // Immutability — 'before' is unchanged.
    expect(before.activationState).toBe("candidate");
    expect(before.promotionHistory).toHaveLength(0);
    expect(validateArtifact(after).valid).toBe(true);
  });

  test("throws when event.from does not match current activationState", () => {
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: "sha256:" + "f".repeat(64),
      provenance: aProvenance,
    });
    // artifact is "candidate"; event claims "from: active"
    const bogus = createPromotionEvent({
      from: "active",
      to: "shadow",
      reason: "bogus",
      timestamp: "2026-04-17T12:10:00.000Z",
    });
    expect(() => appendPromotionEvent(artifact, bogus)).toThrow(/from.*candidate/i);
  });
});
