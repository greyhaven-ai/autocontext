import { describe, test, expect } from "vitest";
import fc from "fast-check";
import { decidePromotion } from "../../../src/control-plane/promotion/decide.js";
import { defaultThresholds } from "../../../src/control-plane/promotion/thresholds.js";
import { createArtifact, createEvalRun } from "../../../src/control-plane/contract/factories.js";
import type {
  Artifact,
  EvalRun,
  MetricBundle,
  Provenance,
  SafetyRegression,
} from "../../../src/control-plane/contract/types.js";

const prov: Provenance = {
  authorType: "human",
  authorId: "t@e",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

function mkMetrics(overrides: Partial<MetricBundle> = {}): MetricBundle {
  const base: MetricBundle = {
    quality: { score: 0.8, sampleSize: 200 },
    cost: { tokensIn: 1000, tokensOut: 500 },
    latency: { p50Ms: 100, p95Ms: 200, p99Ms: 300 },
    safety: { regressions: [] },
    evalRunnerIdentity: {
      name: "eval",
      version: "1.0",
      configHash: "sha256:" + "a".repeat(64),
    },
  };
  return { ...base, ...overrides };
}

function mkArtifact(): Artifact {
  return createArtifact({
    actuatorType: "prompt-patch",
    scenario: "grid_ctf",
    payloadHash: "sha256:" + "b".repeat(64),
    provenance: prov,
  });
}

function mkEvalRun(artifact: Artifact, metrics: MetricBundle): EvalRun {
  return createEvalRun({
    runId: "run_" + artifact.id.slice(0, 6),
    artifactId: artifact.id,
    suiteId: "prod-eval-v3",
    metrics,
    datasetProvenance: {
      datasetId: "ds-1",
      sliceHash: "sha256:" + "c".repeat(64),
      sampleCount: metrics.quality.sampleSize,
    },
    ingestedAt: "2026-04-17T12:05:00.000Z",
  });
}

describe("decidePromotion — example cases", () => {
  test("candidate decisively beats baseline → pass=true, strong → active", () => {
    const baseline = mkArtifact();
    const candidate = mkArtifact();
    const d = decidePromotion({
      candidate: { artifact: candidate, evalRun: mkEvalRun(candidate, mkMetrics({ quality: { score: 0.9, sampleSize: 1000 } })) },
      baseline: { artifact: baseline, evalRun: mkEvalRun(baseline, mkMetrics({ quality: { score: 0.7, sampleSize: 1000 } })) },
      thresholds: defaultThresholds(),
      evaluatedAt: "2026-04-17T12:20:00.000Z",
    });
    expect(d.pass).toBe(true);
    expect(d.recommendedTargetState).toBe("active");
    expect(d.deltas.quality.delta).toBeCloseTo(0.2, 5);
    expect(d.deltas.quality.passed).toBe(true);
    expect(d.deltas.safety.passed).toBe(true);
  });

  test("candidate with safety regression → pass=false, target=disabled regardless of other dims", () => {
    const baseline = mkArtifact();
    const candidate = mkArtifact();
    const reg: SafetyRegression = { id: "r1", severity: "major", description: "broke a thing" };
    const d = decidePromotion({
      candidate: {
        artifact: candidate,
        evalRun: mkEvalRun(candidate, mkMetrics({
          quality: { score: 0.99, sampleSize: 1000 },           // very good
          safety: { regressions: [reg] },                        // but safety broke
        })),
      },
      baseline: { artifact: baseline, evalRun: mkEvalRun(baseline, mkMetrics()) },
      thresholds: defaultThresholds(),
      evaluatedAt: "2026-04-17T12:20:00.000Z",
    });
    expect(d.pass).toBe(false);
    expect(d.recommendedTargetState).toBe("disabled");
    expect(d.deltas.safety.passed).toBe(false);
    expect(d.deltas.safety.regressions).toHaveLength(1);
  });

  test("no baseline (first candidate) → recommendedTargetState=shadow regardless of absolute metrics", () => {
    const candidate = mkArtifact();
    const d = decidePromotion({
      candidate: { artifact: candidate, evalRun: mkEvalRun(candidate, mkMetrics({ quality: { score: 0.99, sampleSize: 1000 } })) },
      baseline: null,
      thresholds: defaultThresholds(),
      evaluatedAt: "2026-04-17T12:20:00.000Z",
    });
    expect(d.pass).toBe(true);
    expect(d.recommendedTargetState).toBe("shadow");
  });

  test("marginal improvement with low confidence → shadow", () => {
    const baseline = mkArtifact();
    const candidate = mkArtifact();
    const t = defaultThresholds();
    const d = decidePromotion({
      candidate: { artifact: candidate, evalRun: mkEvalRun(candidate, mkMetrics({ quality: { score: 0.7 + t.qualityMinDelta, sampleSize: 5 } })) },
      baseline: { artifact: baseline, evalRun: mkEvalRun(baseline, mkMetrics({ quality: { score: 0.7, sampleSize: 5 } })) },
      thresholds: t,
      evaluatedAt: "2026-04-17T12:20:00.000Z",
    });
    expect(d.pass).toBe(true);
    expect(d.recommendedTargetState).toBe("shadow");
  });

  test("cost budget exceeded → cost passed=false → overall pass=false", () => {
    const baseline = mkArtifact();
    const candidate = mkArtifact();
    const t = defaultThresholds();
    const d = decidePromotion({
      candidate: {
        artifact: candidate,
        evalRun: mkEvalRun(candidate, mkMetrics({
          quality: { score: 0.9, sampleSize: 1000 },
          cost: { tokensIn: 1000, tokensOut: 10000 },        // 20x
        })),
      },
      baseline: { artifact: baseline, evalRun: mkEvalRun(baseline, mkMetrics()) },
      thresholds: t,
      evaluatedAt: "2026-04-17T12:20:00.000Z",
    });
    expect(d.deltas.cost.passed).toBe(false);
    expect(d.pass).toBe(false);
  });
});

describe("P3 (property): decidePromotion is deterministic", () => {
  test("same inputs yield byte-identical outputs", () => {
    const baseline = mkArtifact();
    const candidate = mkArtifact();
    const cand = {
      artifact: candidate,
      evalRun: mkEvalRun(candidate, mkMetrics({ quality: { score: 0.85, sampleSize: 500 } })),
    };
    const base = { artifact: baseline, evalRun: mkEvalRun(baseline, mkMetrics()) };
    const t = defaultThresholds();
    const input = { candidate: cand, baseline: base, thresholds: t, evaluatedAt: "2026-04-17T12:20:00.000Z" };
    const a = decidePromotion(input);
    const b = decidePromotion(input);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  test("over random quality/cost/latency inputs, determinism holds", () => {
    fc.assert(
      fc.property(
        fc.record({
          baselineQ: fc.double({ min: 0, max: 1, noNaN: true }),
          candidateQ: fc.double({ min: 0, max: 1, noNaN: true }),
          samples: fc.integer({ min: 1, max: 5000 }),
          baselineCost: fc.integer({ min: 100, max: 10000 }),
          candidateCost: fc.integer({ min: 100, max: 100000 }),
          baselineLat: fc.integer({ min: 10, max: 5000 }),
          candidateLat: fc.integer({ min: 10, max: 50000 }),
        }),
        (p) => {
          const baseline = mkArtifact();
          const candidate = mkArtifact();
          const cand = {
            artifact: candidate,
            evalRun: mkEvalRun(candidate, mkMetrics({
              quality: { score: p.candidateQ, sampleSize: p.samples },
              cost: { tokensIn: 0, tokensOut: p.candidateCost },
              latency: { p50Ms: 0, p95Ms: p.candidateLat, p99Ms: 0 },
            })),
          };
          const base = {
            artifact: baseline,
            evalRun: mkEvalRun(baseline, mkMetrics({
              quality: { score: p.baselineQ, sampleSize: p.samples },
              cost: { tokensIn: 0, tokensOut: p.baselineCost },
              latency: { p50Ms: 0, p95Ms: p.baselineLat, p99Ms: 0 },
            })),
          };
          const input = { candidate: cand, baseline: base, thresholds: defaultThresholds(), evaluatedAt: "2026-04-17T12:20:00.000Z" };
          expect(JSON.stringify(decidePromotion(input))).toBe(JSON.stringify(decidePromotion(input)));
        },
      ),
      { numRuns: 150 },
    );
  });
});

describe("P4 (property): safety monotonicity — any regression forces pass=false", () => {
  test("across random thresholds and other-dim metrics, regressions always fail", () => {
    fc.assert(
      fc.property(
        fc.record({
          candidateQ: fc.double({ min: 0, max: 1, noNaN: true }),
          baselineQ: fc.double({ min: 0, max: 1, noNaN: true }),
          severity: fc.constantFrom<SafetyRegression["severity"]>("info", "minor", "major", "critical"),
          qualityMinDelta: fc.double({ min: -1, max: 1, noNaN: true }),
          costMax: fc.double({ min: 0.001, max: 100, noNaN: true }),
          latencyMax: fc.double({ min: 0.001, max: 100, noNaN: true }),
        }),
        (p) => {
          const baseline = mkArtifact();
          const candidate = mkArtifact();
          const reg: SafetyRegression = { id: "r", severity: p.severity, description: "x" };
          const cand = {
            artifact: candidate,
            evalRun: mkEvalRun(candidate, mkMetrics({
              quality: { score: p.candidateQ, sampleSize: 1000 },
              safety: { regressions: [reg] },
            })),
          };
          const base = {
            artifact: baseline,
            evalRun: mkEvalRun(baseline, mkMetrics({ quality: { score: p.baselineQ, sampleSize: 1000 } })),
          };
          const t = {
            ...defaultThresholds(),
            qualityMinDelta: p.qualityMinDelta,
            costMaxRelativeIncrease: p.costMax,
            latencyMaxRelativeIncrease: p.latencyMax,
          };
          const d = decidePromotion({
            candidate: cand,
            baseline: base,
            thresholds: t,
            evaluatedAt: "2026-04-17T12:20:00.000Z",
          });
          expect(d.pass).toBe(false);
          expect(d.recommendedTargetState).toBe("disabled");
        },
      ),
      { numRuns: 200 },
    );
  });

  test("P4 holds even with no baseline", () => {
    fc.assert(
      fc.property(
        fc.constantFrom<SafetyRegression["severity"]>("info", "minor", "major", "critical"),
        (sev) => {
          const candidate = mkArtifact();
          const reg: SafetyRegression = { id: "r", severity: sev, description: "x" };
          const d = decidePromotion({
            candidate: { artifact: candidate, evalRun: mkEvalRun(candidate, mkMetrics({ safety: { regressions: [reg] } })) },
            baseline: null,
            thresholds: defaultThresholds(),
            evaluatedAt: "2026-04-17T12:20:00.000Z",
          });
          expect(d.pass).toBe(false);
        },
      ),
      { numRuns: 50 },
    );
  });
});
