import { describe, expect, test } from "vitest";
import { createHarnessChangeProposal } from "../../../src/control-plane/contract/factories.js";
import {
  isHarnessChangeSurface,
  isHarnessValidationMode,
} from "../../../src/control-plane/contract/harness-change-proposal.js";
import { decideHarnessChangeProposal } from "../../../src/control-plane/promotion/harness-change-proposal.js";
import { validateHarnessChangeProposal } from "../../../src/control-plane/contract/validators.js";
import type {
  Artifact,
  EvalRun,
  HarnessChangeProposal,
  HarnessChangeDecision,
  MetricBundle,
  Patch,
  PromotionThresholds,
  Provenance,
} from "../../../src/control-plane/contract/types.js";

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

function metrics(score: number, regressions: MetricBundle["safety"]["regressions"] = []): MetricBundle {
  return {
    quality: { score, sampleSize: 1000 },
    cost: { tokensIn: 100, tokensOut: 50 },
    latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
    safety: { regressions },
    evalRunnerIdentity: {
      name: "heldout",
      version: "1.0.0",
      configHash: `sha256:${"9".repeat(64)}`,
    },
  };
}

function artifact(id: Artifact["id"]): Artifact {
  return {
    schemaVersion: "1.0",
    id,
    actuatorType: "prompt-patch",
    scenario: "grid_ctf",
    environmentTag: "production",
    activationState: "candidate",
    payloadHash: `sha256:${id.endsWith("1") ? "a" : "b"}`.padEnd(71, id.endsWith("1") ? "a" : "b") as Artifact["payloadHash"],
    provenance,
    promotionHistory: [],
    evalRuns: [],
  };
}

function evalRun(artifactId: Artifact["id"], runId: string, score: number): EvalRun {
  return {
    schemaVersion: "1.0",
    runId,
    artifactId,
    suiteId: "heldout-suite",
    metrics: metrics(score),
    datasetProvenance: {
      datasetId: "prod-traces",
      sliceHash: `sha256:${"c".repeat(64)}`,
      sampleCount: 1000,
    },
    ingestedAt: "2026-05-13T12:05:00.000Z",
  };
}

function acceptedDecision(): HarnessChangeDecision {
  const candidateArtifact = artifact("01HX0000000000000000000001" as Artifact["id"]);
  const baselineArtifact = artifact("01HX0000000000000000000002" as Artifact["id"]);
  return decideHarnessChangeProposal({
    proposal: proposal(),
    candidate: {
      artifact: candidateArtifact,
      evalRun: evalRun(candidateArtifact.id, "candidate-heldout", 0.88),
    },
    baseline: {
      artifact: baselineArtifact,
      evalRun: evalRun(baselineArtifact.id, "baseline-heldout", 0.70),
    },
    thresholds,
    validation: {
      mode: "heldout",
      suiteId: "heldout-suite",
      evidenceRefs: ["runs/heldout/candidate-heldout.json"],
    },
    decidedAt: "2026-05-13T12:10:00.000Z",
  });
}

function proposal(overrides: Partial<HarnessChangeProposal> = {}): HarnessChangeProposal {
  return createHarnessChangeProposal({
    id: "01HX0000000000000000000680" as HarnessChangeProposal["id"],
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
    expect(result.errors.some((error) => error.includes("findingIds"))).toBe(true);
  });

  test("validation enforces status and decision lifecycle invariants", () => {
    const decision = acceptedDecision();

    expect(validateHarnessChangeProposal(proposal({ status: "accepted" })).valid).toBe(false);
    expect(validateHarnessChangeProposal(proposal({ status: "proposed", decision })).valid).toBe(false);
    expect(validateHarnessChangeProposal(proposal({ status: "rejected", decision })).valid).toBe(false);
    expect(validateHarnessChangeProposal(proposal({ decision })).valid).toBe(true);
  });

  test("accepts only when candidate beats baseline on heldout or fresh validation", () => {
    const decision = acceptedDecision();

    expect(decision.status).toBe("accepted");
    expect(decision.promotionDecision.pass).toBe(true);
    expect(decision.reason).toContain("heldout");
  });

  test("marks dev-only validation inconclusive even when candidate improves", () => {
    const candidateArtifact = artifact("01HX0000000000000000000001" as Artifact["id"]);
    const baselineArtifact = artifact("01HX0000000000000000000002" as Artifact["id"]);
    const decision = decideHarnessChangeProposal({
      proposal: proposal(),
      candidate: {
        artifact: candidateArtifact,
        evalRun: evalRun(candidateArtifact.id, "candidate-dev", 0.88),
      },
      baseline: {
        artifact: baselineArtifact,
        evalRun: evalRun(baselineArtifact.id, "baseline-dev", 0.70),
      },
      thresholds,
      validation: {
        mode: "dev",
        suiteId: "dev-suite",
        evidenceRefs: ["runs/dev/candidate-dev.json"],
      },
      decidedAt: "2026-05-13T12:10:00.000Z",
    });

    expect(decision.status).toBe("inconclusive");
    expect(decision.reason).toContain("heldout or fresh");
  });
});
