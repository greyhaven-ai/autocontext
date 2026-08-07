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
import type {
  AblationVerification,
  Provenance,
  MetricBundle,
  StrategyIdentity,
  StrategyQuarantine,
} from "../../../src/control-plane/contract/types.js";
import {
  parseArtifactId,
  parseContentHash,
  parseEnvironmentTag,
  parseScenario,
  parseSuiteId,
  type ArtifactId,
  type ContentHash,
  type EnvironmentTag,
  type Scenario,
  type SuiteId,
} from "../../../src/control-plane/contract/branded-ids.js";

function hash(fill: string): ContentHash {
  const parsed = parseContentHash(`sha256:${fill.repeat(64)}`);
  if (parsed === null) throw new Error(`invalid test hash fill: ${fill}`);
  return parsed;
}

function artifactId(value: string): ArtifactId {
  const parsed = parseArtifactId(value);
  if (parsed === null) throw new Error(`invalid test artifact id: ${value}`);
  return parsed;
}

function scenario(value: string): Scenario {
  const parsed = parseScenario(value);
  if (parsed === null) throw new Error(`invalid test scenario: ${value}`);
  return parsed;
}

function envTag(value: string): EnvironmentTag {
  const parsed = parseEnvironmentTag(value);
  if (parsed === null) throw new Error(`invalid test environment tag: ${value}`);
  return parsed;
}

function suiteId(value: string): SuiteId {
  const parsed = parseSuiteId(value);
  if (parsed === null) throw new Error(`invalid test suite id: ${value}`);
  return parsed;
}

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
    configHash: hash("a"),
  },
};

describe("createArtifact", () => {
  test("produces a valid Artifact in candidate state with fresh ULID and defaults", () => {
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: scenario("grid_ctf"),
      payloadHash: hash("b"),
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

  test("preserves optional strategy identity metadata", () => {
    const strategyIdentity: StrategyIdentity = {
      fingerprint: hash("9"),
      components: [{ name: "prompt.txt", fingerprint: hash("8") }],
      lineage: {
        parentFingerprints: [hash("7")],
      },
    };
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: scenario("grid_ctf"),
      payloadHash: hash("b"),
      provenance: aProvenance,
      strategyIdentity,
    });

    expect(artifact.strategyIdentity).toEqual(strategyIdentity);
    expect(validateArtifact(artifact).valid).toBe(true);
  });

  test("preserves optional strategy quarantine metadata", () => {
    const strategyQuarantine: StrategyQuarantine = {
      status: "quarantined",
      reason: "repeated-invalid-strategy",
      sourceArtifactIds: [artifactId("01KPEYB3BQNFDEYRS8KH538PF5")],
      sourceFingerprints: [hash("7")],
      detail: "exact duplicate of disabled strategy",
    };
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: scenario("grid_ctf"),
      payloadHash: hash("b"),
      provenance: aProvenance,
      strategyQuarantine,
    });

    expect(artifact.strategyQuarantine).toEqual(strategyQuarantine);
    expect(validateArtifact(artifact).valid).toBe(true);
  });

  test("respects overrides for id and environmentTag (for tests / legacy adapter)", () => {
    const artifact = createArtifact({
      actuatorType: "tool-policy",
      scenario: scenario("othello"),
      environmentTag: envTag("staging"),
      payloadHash: hash("c"),
      provenance: aProvenance,
      id: artifactId("01KPEYB3BQNFDEYRS8KH538PF5"),
    });
    expect(artifact.id).toBe("01KPEYB3BQNFDEYRS8KH538PF5");
    expect(artifact.environmentTag).toBe("staging");
    expect(validateArtifact(artifact).valid).toBe(true);
  });

  test("different invocations produce different ULIDs (time-ordered)", () => {
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: scenario("grid_ctf"),
      payloadHash: hash("d"),
      provenance: aProvenance,
    });
    const b = createArtifact({
      actuatorType: "prompt-patch",
      scenario: scenario("grid_ctf"),
      payloadHash: hash("d"),
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
      evidence: { suiteId: suiteId("prod-eval-v3") },
      signature: "sig-abc",
    });
    expect(event.evidence).toEqual({ suiteId: suiteId("prod-eval-v3") });
    expect(event.signature).toBe("sig-abc");
    expect(validatePromotionEvent(event).valid).toBe(true);
  });
});

describe("createEvalRun", () => {
  test("produces a valid EvalRun", () => {
    const run = createEvalRun({
      runId: "eval_123",
      artifactId: artifactId("01KPEYB3BRQWK2WSHK9E93N6NP"),
      suiteId: suiteId("prod-eval-v3"),
      metrics: aMetricBundle,
      datasetProvenance: {
        datasetId: "prod-traces-2026-04-15",
        sliceHash: hash("e"),
        sampleCount: 300,
      },
      ingestedAt: "2026-04-17T12:05:00.000Z",
    });
    expect(run.schemaVersion).toBe("1.0");
    expect(validateEvalRun(run).valid).toBe(true);
  });

  test("preserves optional ablation verification evidence", () => {
    const ablationVerification: AblationVerification = {
      status: "passed",
      targets: ["strategy", "harness"],
      verifiedAt: "2026-05-13T12:00:00.000Z",
      evidenceRefs: ["runs/ablation/run_1.json"],
    };
    const run = createEvalRun({
      runId: "eval_ablation",
      artifactId: artifactId("01KPEYB3BRQWK2WSHK9E93N6NP"),
      suiteId: suiteId("prod-eval-v3"),
      metrics: aMetricBundle,
      datasetProvenance: {
        datasetId: "prod-traces-2026-04-15",
        sliceHash: hash("e"),
        sampleCount: 300,
      },
      ingestedAt: "2026-04-17T12:05:00.000Z",
      ablationVerification,
    });

    expect(run.ablationVerification).toEqual(ablationVerification);
    expect(validateEvalRun(run).valid).toBe(true);
  });
});

describe("appendPromotionEvent (immutable, state-transition enforcing)", () => {
  test("returns a new Artifact with the event appended and activationState updated", () => {
    const before = createArtifact({
      actuatorType: "prompt-patch",
      scenario: scenario("grid_ctf"),
      payloadHash: hash("f"),
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
      scenario: scenario("grid_ctf"),
      payloadHash: hash("f"),
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
