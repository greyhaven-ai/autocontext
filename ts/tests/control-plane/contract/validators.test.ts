import { describe, test, expect } from "vitest";
import {
  validateMetricBundle,
  validateProvenance,
  validateEvalRun,
  validatePromotionEvent,
  validateArtifact,
  validatePromotionDecision,
  validatePatch,
} from "../../../src/control-plane/contract/validators.js";
import type {
  MetricBundle,
  Provenance,
  EvalRun,
  PromotionEvent,
  Artifact,
  PromotionDecision,
  Patch,
} from "../../../src/control-plane/contract/types.js";

const validMetricBundle: MetricBundle = {
  quality: { score: 0.8, sampleSize: 100 },
  cost: { tokensIn: 1000, tokensOut: 500, usd: 0.02 },
  latency: { p50Ms: 100, p95Ms: 200, p99Ms: 300 },
  safety: { regressions: [] },
  humanFeedback: { positive: 10, negative: 2, neutral: 5 },
  evalRunnerIdentity: {
    name: "my-eval",
    version: "1.0.0",
    configHash: "sha256:" + "a".repeat(64),
  },
};

const validProvenance: Provenance = {
  authorType: "autocontext-run",
  authorId: "run_abc",
  agentRole: "architect",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

const validEvalRun: EvalRun = {
  schemaVersion: "1.0",
  runId: "eval_abc",
  artifactId: "01KPEYB3BQNFDEYRS8KH538PF5",
  suiteId: "prod-eval-v3",
  metrics: validMetricBundle,
  datasetProvenance: {
    datasetId: "prod-traces-2026-04-15",
    sliceHash: "sha256:" + "b".repeat(64),
    sampleCount: 300,
  },
  ingestedAt: "2026-04-17T12:05:00.000Z",
};

const validPromotionEvent: PromotionEvent = {
  from: "candidate",
  to: "shadow",
  reason: "first eval passed shadow threshold",
  timestamp: "2026-04-17T12:10:00.000Z",
};

const validArtifact: Artifact = {
  schemaVersion: "1.0",
  id: "01KPEYB3BRQWK2WSHK9E93N6NP",
  actuatorType: "prompt-patch",
  scenario: "grid_ctf",
  environmentTag: "production",
  activationState: "candidate",
  payloadHash: "sha256:" + "c".repeat(64),
  provenance: validProvenance,
  promotionHistory: [],
  evalRuns: [],
};

const validPromotionDecision: PromotionDecision = {
  schemaVersion: "1.0",
  pass: true,
  recommendedTargetState: "canary",
  deltas: {
    quality: { baseline: 0.7, candidate: 0.8, delta: 0.1, passed: true },
    cost: {
      baseline: { tokensIn: 900, tokensOut: 450 },
      candidate: { tokensIn: 1000, tokensOut: 500 },
      delta: { tokensIn: 100, tokensOut: 50 },
      passed: true,
    },
    latency: {
      baseline: { p50Ms: 100, p95Ms: 200, p99Ms: 300 },
      candidate: { p50Ms: 110, p95Ms: 210, p99Ms: 320 },
      delta: { p50Ms: 10, p95Ms: 10, p99Ms: 20 },
      passed: true,
    },
    safety: { regressions: [], passed: true },
  },
  confidence: 0.85,
  thresholds: {
    qualityMinDelta: 0.05,
    costMaxRelativeIncrease: 0.2,
    latencyMaxRelativeIncrease: 0.2,
    strongConfidenceMin: 0.9,
    moderateConfidenceMin: 0.7,
    strongQualityMultiplier: 2.0,
  },
  reasoning: "Candidate passed quality, cost, and latency with moderate confidence.",
  evaluatedAt: "2026-04-17T12:20:00.000Z",
};

const validPatch: Patch = {
  filePath: "agents/grid_ctf/prompts/competitor.txt",
  operation: "modify",
  unifiedDiff: "--- a/competitor.txt\n+++ b/competitor.txt\n@@ -1 +1 @@\n-old\n+new\n",
  afterContent: "new\n",
};

// ---------- validator behavior ----------

describe("validateMetricBundle", () => {
  test("accepts a valid bundle", () => {
    expect(validateMetricBundle(validMetricBundle).valid).toBe(true);
  });

  test("rejects missing required dimension", () => {
    const bad = { ...validMetricBundle } as Partial<MetricBundle>;
    delete bad.quality;
    const r = validateMetricBundle(bad);
    expect(r.valid).toBe(false);
    expect(r.errors?.some((e) => /quality/.test(e))).toBe(true);
  });

  test("rejects wrong type for quality.score", () => {
    const bad = { ...validMetricBundle, quality: { score: "high", sampleSize: 10 } } as unknown as MetricBundle;
    expect(validateMetricBundle(bad).valid).toBe(false);
  });

  test("accepts bundle without optional humanFeedback", () => {
    const { humanFeedback: _hf, ...rest } = validMetricBundle;
    expect(validateMetricBundle(rest as MetricBundle).valid).toBe(true);
  });
});

describe("validateProvenance", () => {
  test("accepts valid", () => {
    expect(validateProvenance(validProvenance).valid).toBe(true);
  });

  test("rejects invalid authorType", () => {
    const bad = { ...validProvenance, authorType: "aliens" } as unknown as Provenance;
    expect(validateProvenance(bad).valid).toBe(false);
  });

  test("rejects missing parentArtifactIds array", () => {
    const bad = { ...validProvenance } as Partial<Provenance>;
    delete bad.parentArtifactIds;
    expect(validateProvenance(bad).valid).toBe(false);
  });
});

describe("validateEvalRun", () => {
  test("accepts valid", () => {
    expect(validateEvalRun(validEvalRun).valid).toBe(true);
  });

  test("rejects invalid artifactId format", () => {
    const bad = { ...validEvalRun, artifactId: "not-a-ulid" } as unknown as EvalRun;
    expect(validateEvalRun(bad).valid).toBe(false);
  });

  test("rejects missing schemaVersion", () => {
    const bad = { ...validEvalRun } as Partial<EvalRun>;
    delete bad.schemaVersion;
    expect(validateEvalRun(bad).valid).toBe(false);
  });
});

describe("validatePromotionEvent", () => {
  test("accepts valid", () => {
    expect(validatePromotionEvent(validPromotionEvent).valid).toBe(true);
  });

  test("rejects invalid from/to state", () => {
    const bad = { ...validPromotionEvent, from: "unknown-state" } as unknown as PromotionEvent;
    expect(validatePromotionEvent(bad).valid).toBe(false);
  });

  test("accepts with optional evidence and signature", () => {
    const withExtras: PromotionEvent = {
      ...validPromotionEvent,
      evidence: { suiteId: "prod-eval-v3", baselineArtifactId: "01KPEYB3BRYCQ6J235VBR7WBY8" },
      signature: "abc123",
    };
    expect(validatePromotionEvent(withExtras).valid).toBe(true);
  });
});

describe("validateArtifact", () => {
  test("accepts valid", () => {
    expect(validateArtifact(validArtifact).valid).toBe(true);
  });

  test("rejects invalid actuatorType", () => {
    const bad = { ...validArtifact, actuatorType: "teleport" } as unknown as Artifact;
    expect(validateArtifact(bad).valid).toBe(false);
  });

  test("rejects invalid payloadHash format", () => {
    const bad = { ...validArtifact, payloadHash: "md5:xxx" } as unknown as Artifact;
    expect(validateArtifact(bad).valid).toBe(false);
  });
});

describe("validatePromotionDecision", () => {
  test("accepts valid", () => {
    expect(validatePromotionDecision(validPromotionDecision).valid).toBe(true);
  });

  test("rejects invalid recommendedTargetState", () => {
    const bad = { ...validPromotionDecision, recommendedTargetState: "production" } as unknown as PromotionDecision;
    expect(validatePromotionDecision(bad).valid).toBe(false);
  });
});

describe("validatePatch", () => {
  test("accepts a modify patch", () => {
    expect(validatePatch(validPatch).valid).toBe(true);
  });

  test("accepts a create patch with afterContent", () => {
    const p: Patch = { filePath: "x.txt", operation: "create", unifiedDiff: "diff", afterContent: "new" };
    expect(validatePatch(p).valid).toBe(true);
  });

  test("accepts a delete patch without afterContent", () => {
    const p: Patch = { filePath: "x.txt", operation: "delete", unifiedDiff: "diff" };
    expect(validatePatch(p).valid).toBe(true);
  });

  test("rejects unknown operation", () => {
    const bad = { ...validPatch, operation: "move" } as unknown as Patch;
    expect(validatePatch(bad).valid).toBe(false);
  });
});

describe("round-trip: encode → parse → validate → deep-equal", () => {
  test("Artifact survives JSON round-trip", () => {
    const json = JSON.stringify(validArtifact);
    const parsed = JSON.parse(json);
    expect(validateArtifact(parsed).valid).toBe(true);
    expect(parsed).toStrictEqual(validArtifact);
  });

  test("EvalRun survives JSON round-trip", () => {
    const json = JSON.stringify(validEvalRun);
    const parsed = JSON.parse(json);
    expect(validateEvalRun(parsed).valid).toBe(true);
    expect(parsed).toStrictEqual(validEvalRun);
  });

  test("PromotionDecision survives JSON round-trip", () => {
    const json = JSON.stringify(validPromotionDecision);
    const parsed = JSON.parse(json);
    expect(validatePromotionDecision(parsed).valid).toBe(true);
    expect(parsed).toStrictEqual(validPromotionDecision);
  });
});
