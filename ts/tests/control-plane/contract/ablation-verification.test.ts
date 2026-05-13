import { describe, expect, test } from "vitest";
import {
  assessAblationVerification,
  describeAblationVerificationIssue,
} from "../../../src/control-plane/contract/ablation-verification.js";
import type {
  AblationRequirement,
  AblationVerification,
  EvalRun,
  MetricBundle,
} from "../../../src/control-plane/contract/types.js";
import { createEvalRun } from "../../../src/control-plane/contract/factories.js";

const metrics: MetricBundle = {
  quality: { score: 0.9, sampleSize: 100 },
  cost: { tokensIn: 1000, tokensOut: 500 },
  latency: { p50Ms: 100, p95Ms: 200, p99Ms: 300 },
  safety: { regressions: [] },
  evalRunnerIdentity: {
    name: "eval",
    version: "1.0",
    configHash: "sha256:" + "9".repeat(64),
  },
};

const requirement: AblationRequirement = {
  required: true,
  targets: ["strategy", "harness"],
};

const passedVerification: AblationVerification = {
  status: "passed",
  targets: ["strategy", "harness"],
  verifiedAt: "2026-05-13T12:00:00.000Z",
  evidenceRefs: ["runs/ablation/run_1.json"],
};

function evalRun(ablationVerification?: AblationVerification): EvalRun {
  return createEvalRun({
    runId: "run_1",
    artifactId: "01KPEYB3BRQWK2WSHK9E93N6NP",
    suiteId: "prod-eval-v3",
    metrics,
    datasetProvenance: {
      datasetId: "ds-1",
      sliceHash: "sha256:" + "a".repeat(64),
      sampleCount: 100,
    },
    ingestedAt: "2026-05-13T12:05:00.000Z",
    ...(ablationVerification !== undefined ? { ablationVerification } : {}),
  });
}

describe("ablation verification assessment", () => {
  test("does nothing when ablation is not required", () => {
    const assessment = assessAblationVerification(evalRun(), "candidate", {
      required: false,
      targets: ["strategy", "harness"],
    });

    expect(assessment.status).toBe("not-required");
    expect(describeAblationVerificationIssue(evalRun(), "candidate", {
      required: false,
      targets: ["strategy", "harness"],
    })).toBeNull();
  });

  test("reports missing evidence when the opt-in requirement is enabled", () => {
    const assessment = assessAblationVerification(evalRun(), "candidate", requirement);

    expect(assessment.status).toBe("missing");
    expect(assessment.missingTargets).toEqual(["strategy", "harness"]);
    expect(describeAblationVerificationIssue(evalRun(), "candidate", requirement)).toContain(
      "candidate EvalRun is missing required ablation verification",
    );
  });

  test("rejects failed or incomplete verification statuses", () => {
    expect(assessAblationVerification(evalRun({
      ...passedVerification,
      status: "failed",
    }), "candidate", requirement).status).toBe("failed");
    expect(describeAblationVerificationIssue(evalRun({
      ...passedVerification,
      status: "incomplete",
    }), "candidate", requirement)).toContain("status is incomplete");
  });

  test("requires every configured ablation target to be covered", () => {
    const assessment = assessAblationVerification(evalRun({
      ...passedVerification,
      targets: ["strategy"],
    }), "candidate", requirement);

    expect(assessment.status).toBe("incomplete");
    expect(assessment.coveredTargets).toEqual(["strategy"]);
    expect(assessment.missingTargets).toEqual(["harness"]);
    expect(assessment.reason).toContain("harness");
  });

  test("passes when status and target coverage satisfy the requirement", () => {
    const assessment = assessAblationVerification(evalRun(passedVerification), "candidate", requirement);

    expect(assessment).toEqual({
      required: true,
      status: "passed",
      requiredTargets: ["strategy", "harness"],
      coveredTargets: ["strategy", "harness"],
      missingTargets: [],
    });
  });
});
