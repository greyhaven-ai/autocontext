import { describe, expect, test } from "vitest";
import {
  assessEvalRunTrack,
  effectiveEvalRunTrack,
  isRunTrack,
} from "../../../src/control-plane/contract/run-track.js";
import { createEvalRun } from "../../../src/control-plane/contract/factories.js";
import type {
  ArtifactId,
  ContentHash,
  SuiteId,
} from "../../../src/control-plane/contract/branded-ids.js";
import type { EvalRun, MetricBundle } from "../../../src/control-plane/contract/types.js";

const metrics: MetricBundle = {
  quality: { score: 0.8, sampleSize: 10 },
  cost: { tokensIn: 10, tokensOut: 5 },
  latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
  safety: { regressions: [] },
  evalRunnerIdentity: {
    name: "eval",
    version: "1.0",
    configHash: ("sha256:" + "a".repeat(64)) as ContentHash,
  },
};

function makeEvalRun(overrides: Partial<EvalRun> = {}): EvalRun {
  return {
    ...createEvalRun({
      runId: "run_1",
      artifactId: "01KPEYB3BQNFDEYRS8KH538PF5" as ArtifactId,
      suiteId: "prod-eval" as SuiteId,
      metrics,
      datasetProvenance: {
        datasetId: "ds-1",
        sliceHash: ("sha256:" + "b".repeat(64)) as ContentHash,
        sampleCount: 10,
      },
      ingestedAt: "2026-04-17T12:05:00.000Z",
    }),
    ...overrides,
  };
}

describe("run track domain", () => {
  test("recognizes only supported tracks", () => {
    expect(isRunTrack("verified")).toBe(true);
    expect(isRunTrack("experimental")).toBe(true);
    expect(isRunTrack("record")).toBe(false);
  });

  test("defaults legacy clean EvalRuns to verified", () => {
    expect(effectiveEvalRunTrack(makeEvalRun())).toBe("verified");
  });

  test("honors explicit experimental track", () => {
    expect(effectiveEvalRunTrack(makeEvalRun({ track: "experimental" }))).toBe("experimental");
  });

  test("downgrades non-clean integrity to experimental for reporting", () => {
    expect(
      effectiveEvalRunTrack(makeEvalRun({ integrity: { status: "contaminated" } })),
    ).toBe("experimental");
  });

  test("marks explicit experimental evidence as promotion-ineligible", () => {
    const assessment = assessEvalRunTrack(makeEvalRun({ track: "experimental" }), "candidate");

    expect(assessment.track).toBe("experimental");
    expect(assessment.promotionEligible).toBe(false);
    expect(assessment.reasons).toContain("candidate EvalRun track is experimental");
  });

  test("keeps clean verified evidence promotion-eligible while warning on missing metadata", () => {
    const assessment = assessEvalRunTrack(makeEvalRun({ track: "verified" }), "candidate");

    expect(assessment.track).toBe("verified");
    expect(assessment.promotionEligible).toBe(true);
    expect(assessment.warnings).toContain("candidate EvalRun is missing adapter provenance");
    expect(assessment.warnings).toContain("candidate EvalRun is missing score reconciliation");
  });
});
