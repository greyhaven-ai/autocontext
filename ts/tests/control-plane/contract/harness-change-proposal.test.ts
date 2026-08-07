import { describe, expect, test } from "vitest";
import { createHarnessChangeProposal } from "../../../src/control-plane/contract/factories.js";
import {
  isHarnessChangeSurface,
  isHarnessValidationMode,
} from "../../../src/control-plane/contract/harness-change-proposal.js";
import { decideHarnessChangeProposal } from "../../../src/control-plane/promotion/harness-change-proposal.js";
import { validateHarnessChangeProposal } from "../../../src/control-plane/contract/validators.js";
import {
  parseArtifactId,
  parseContentHash,
  parseEnvironmentTag,
  parseHarnessProposalId,
  parseScenario,
  parseSuiteId,
} from "../../../src/control-plane/contract/branded-ids.js";
import { parseSchemaVersion } from "../../../src/control-plane/contract/schema-version.js";
import type {
  Artifact,
  EvalRun,
  HarnessChangeProposal,
  HarnessChangeDecision,
  MetricBundle,
  Patch,
  PromotionThresholds,
  Provenance,
  ValidationResult,
} from "../../../src/control-plane/contract/types.js";

// ValidationResult is a discriminated union: `errors` only exists on the
// failure branch, so narrow before reading it.
function failureErrors(result: ValidationResult): readonly string[] {
  if (result.valid) throw new Error("expected validation to fail");
  return result.errors;
}

const provenance: Provenance = {
  authorType: "autocontext-run",
  authorId: "run_680",
  parentArtifactIds: [],
  createdAt: "2026-05-13T12:00:00.000Z",
};

const patch: Patch = {
  filePath: "agents/grid_ctf/prompts/competitor.txt",
  operation: "modify",
  unifiedDiff: "--- a/competitor.txt\n+++ b/competitor.txt\n@@ -1 +1 @@\n-old\n+new\n",
  afterContent: "new\n",
};

const thresholds: PromotionThresholds = {
  qualityMinDelta: 0.05,
  costMaxRelativeIncrease: 0.2,
  latencyMaxRelativeIncrease: 0.2,
  strongConfidenceMin: 0.9,
  moderateConfidenceMin: 0.7,
  strongQualityMultiplier: 2.0,
};

function metrics(
  score: number,
  regressions: MetricBundle["safety"]["regressions"] = [],
): MetricBundle {
  return {
    quality: { score, sampleSize: 1000 },
    cost: { tokensIn: 100, tokensOut: 50 },
    latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
    safety: { regressions },
    evalRunnerIdentity: {
      name: "heldout",
      version: "1.0.0",
      configHash: parseContentHash(`sha256:${"9".repeat(64)}`)!,
    },
  };
}

function artifact(id: Artifact["id"]): Artifact {
  return {
    schemaVersion: parseSchemaVersion("1.0")!,
    id,
    actuatorType: "prompt-patch",
    scenario: parseScenario("grid_ctf")!,
    environmentTag: parseEnvironmentTag("production")!,
    activationState: "candidate",
    payloadHash: parseContentHash(
      `sha256:${id.endsWith("1") ? "a" : "b"}`.padEnd(71, id.endsWith("1") ? "a" : "b"),
    )!,
    provenance,
    promotionHistory: [],
    evalRuns: [],
  };
}

function evalRun(artifactId: Artifact["id"], runId: string, score: number): EvalRun {
  return {
    schemaVersion: parseSchemaVersion("1.0")!,
    runId,
    artifactId,
    suiteId: parseSuiteId("heldout-suite")!,
    metrics: metrics(score),
    datasetProvenance: {
      datasetId: "prod-traces",
      sliceHash: parseContentHash(`sha256:${"c".repeat(64)}`)!,
      sampleCount: 1000,
    },
    ingestedAt: "2026-05-13T12:05:00.000Z",
  };
}

function acceptedDecision(
  evidenceRefs: readonly string[] = ["runs/heldout/candidate-heldout.json"],
): HarnessChangeDecision {
  const candidateArtifact = artifact(parseArtifactId("01HX0000000000000000000001")!);
  const baselineArtifact = artifact(parseArtifactId("01HX0000000000000000000002")!);
  return decideHarnessChangeProposal({
    proposal: proposal(),
    candidate: {
      artifact: candidateArtifact,
      evalRun: evalRun(candidateArtifact.id, "candidate-heldout", 0.88),
    },
    baseline: {
      artifact: baselineArtifact,
      evalRun: evalRun(baselineArtifact.id, "baseline-heldout", 0.7),
    },
    thresholds,
    validation: {
      mode: "heldout",
      suiteId: parseSuiteId("heldout-suite")!,
      evidenceRefs,
    },
    decidedAt: "2026-05-13T12:10:00.000Z",
  });
}

function proposal(overrides: Partial<HarnessChangeProposal> = {}): HarnessChangeProposal {
  return createHarnessChangeProposal({
    id: parseHarnessProposalId("01HX0000000000000000000680")!,
    findingIds: ["finding-1"],
    targetSurface: "prompt",
    proposedEdit: {
      summary: "Tighten the capture-the-flag prompt around legal moves.",
      patches: [patch],
    },
    expectedImpact: {
      qualityDelta: 0.08,
      riskReduction: "Reduces verifier gaming by forcing evidence-backed moves.",
    },
    rollbackCriteria: ["Candidate loses heldout quality edge."],
    provenance,
    ...overrides,
  });
}

describe("harness change proposal contract", () => {
  test("recognizes supported surfaces and validation modes", () => {
    expect(isHarnessChangeSurface("prompt")).toBe(true);
    expect(isHarnessChangeSurface("tool-schema")).toBe(true);
    expect(isHarnessChangeSurface("database")).toBe(false);
    expect(isHarnessValidationMode("heldout")).toBe(true);
    expect(isHarnessValidationMode("fresh")).toBe(true);
    expect(isHarnessValidationMode("dev")).toBe(true);
    expect(isHarnessValidationMode("leaderboard")).toBe(false);
  });

  test("factory creates a valid durable proposal artifact", () => {
    const created = proposal();
    expect(created.status).toBe("proposed");
    expect(created.findingIds).toEqual(["finding-1"]);
    expect(validateHarnessChangeProposal(created).valid).toBe(true);
  });

  test("validation rejects proposals without finding lineage", () => {
    const invalid = proposal({ findingIds: [] });
    const result = validateHarnessChangeProposal(invalid);
    expect(result.valid).toBe(false);
    expect(failureErrors(result).some((error) => error.includes("findingIds"))).toBe(true);
  });

  test("validation enforces status and decision lifecycle invariants", () => {
    const decision = acceptedDecision();

    expect(validateHarnessChangeProposal(proposal({ status: "accepted" })).valid).toBe(false);
    expect(validateHarnessChangeProposal(proposal({ status: "proposed", decision })).valid).toBe(
      false,
    );
    expect(validateHarnessChangeProposal(proposal({ status: "rejected", decision })).valid).toBe(
      false,
    );
    expect(validateHarnessChangeProposal(proposal({ decision })).valid).toBe(true);
  });

  test("validation rejects accepted or rejected decisions without evidence refs", () => {
    const acceptedWithoutRefs: HarnessChangeDecision = {
      ...acceptedDecision(),
      validation: {
        mode: "heldout",
        suiteId: parseSuiteId("heldout-suite")!,
        evidenceRefs: [],
      },
    };
    const acceptedResult = validateHarnessChangeProposal(
      proposal({ decision: acceptedWithoutRefs }),
    );
    expect(acceptedResult.valid).toBe(false);
    expect(failureErrors(acceptedResult).some((error) => error.includes("evidenceRefs"))).toBe(true);

    const rejectedWithoutRefs: HarnessChangeDecision = {
      ...acceptedWithoutRefs,
      status: "rejected",
      reason: "Rejected on heldout validation.",
    };
    const rejectedResult = validateHarnessChangeProposal(
      proposal({ decision: rejectedWithoutRefs }),
    );
    expect(rejectedResult.valid).toBe(false);
    expect(failureErrors(rejectedResult).some((error) => error.includes("evidenceRefs"))).toBe(true);
  });

  test("validation rejects accepted or rejected decisions from dev-only evidence", () => {
    const acceptedFromDev: HarnessChangeDecision = {
      ...acceptedDecision(),
      validation: {
        mode: "dev",
        suiteId: parseSuiteId("dev-suite")!,
        evidenceRefs: ["runs/dev/candidate-dev.json"],
      },
    };
    const acceptedResult = validateHarnessChangeProposal(proposal({ decision: acceptedFromDev }));
    expect(acceptedResult.valid).toBe(false);
    expect(failureErrors(acceptedResult).some((error) => error.includes("mode"))).toBe(true);

    const rejectedFromDev: HarnessChangeDecision = {
      ...acceptedFromDev,
      status: "rejected",
      reason: "Rejected on dev validation.",
    };
    const rejectedResult = validateHarnessChangeProposal(proposal({ decision: rejectedFromDev }));
    expect(rejectedResult.valid).toBe(false);
    expect(failureErrors(rejectedResult).some((error) => error.includes("mode"))).toBe(true);
  });

  test("validation rejects accepted or rejected decisions without baseline evidence", () => {
    const {
      baselineArtifactId: _acceptedBaselineArtifactId,
      baselineEvalRunId: _acceptedBaselineEvalRunId,
      ...acceptedWithoutBaseline
    } = acceptedDecision();
    const acceptedResult = validateHarnessChangeProposal(
      proposal({ decision: acceptedWithoutBaseline }),
    );
    expect(acceptedResult.valid).toBe(false);
    expect(failureErrors(acceptedResult).some((error) => error.includes("baselineArtifactId"))).toBe(true);
    expect(failureErrors(acceptedResult).some((error) => error.includes("baselineEvalRunId"))).toBe(true);

    const {
      baselineArtifactId: _rejectedBaselineArtifactId,
      baselineEvalRunId: _rejectedBaselineEvalRunId,
      ...rejectedWithoutBaseline
    }: HarnessChangeDecision = {
      ...acceptedDecision(),
      status: "rejected",
      reason: "Rejected on heldout validation.",
    };
    const rejectedResult = validateHarnessChangeProposal(
      proposal({ decision: rejectedWithoutBaseline }),
    );
    expect(rejectedResult.valid).toBe(false);
    expect(failureErrors(rejectedResult).some((error) => error.includes("baselineArtifactId"))).toBe(true);
    expect(failureErrors(rejectedResult).some((error) => error.includes("baselineEvalRunId"))).toBe(true);
  });

  test("accepts only when candidate beats baseline on heldout or fresh validation", () => {
    const decision = acceptedDecision();

    expect(decision.status).toBe("accepted");
    expect(decision.promotionDecision.pass).toBe(true);
    expect(decision.reason).toContain("heldout");
  });

  test("marks promotion-grade validation without evidence refs inconclusive", () => {
    const candidateArtifact = artifact(parseArtifactId("01HX0000000000000000000001")!);
    const baselineArtifact = artifact(parseArtifactId("01HX0000000000000000000002")!);
    const decision = decideHarnessChangeProposal({
      proposal: proposal(),
      candidate: {
        artifact: candidateArtifact,
        evalRun: evalRun(candidateArtifact.id, "candidate-heldout", 0.88),
      },
      baseline: {
        artifact: baselineArtifact,
        evalRun: evalRun(baselineArtifact.id, "baseline-heldout", 0.7),
      },
      thresholds,
      validation: {
        mode: "heldout",
        suiteId: parseSuiteId("heldout-suite")!,
        evidenceRefs: [],
      },
      decidedAt: "2026-05-13T12:10:00.000Z",
    });

    expect(decision.status).toBe("inconclusive");
    expect(decision.reason).toContain("evidence reference");
  });

  test("marks dev-only validation inconclusive even when candidate improves", () => {
    const candidateArtifact = artifact(parseArtifactId("01HX0000000000000000000001")!);
    const baselineArtifact = artifact(parseArtifactId("01HX0000000000000000000002")!);
    const decision = decideHarnessChangeProposal({
      proposal: proposal(),
      candidate: {
        artifact: candidateArtifact,
        evalRun: evalRun(candidateArtifact.id, "candidate-dev", 0.88),
      },
      baseline: {
        artifact: baselineArtifact,
        evalRun: evalRun(baselineArtifact.id, "baseline-dev", 0.7),
      },
      thresholds,
      validation: {
        mode: "dev",
        suiteId: parseSuiteId("dev-suite")!,
        evidenceRefs: ["runs/dev/candidate-dev.json"],
      },
      decidedAt: "2026-05-13T12:10:00.000Z",
    });

    expect(decision.status).toBe("inconclusive");
    expect(decision.reason).toContain("heldout or fresh");
  });
});
