import { describe, expect, it } from "vitest";

import {
  buildShadowPromotionCheck,
  DEFAULT_PROMOTION_THRESHOLDS,
  evaluatePromotionCheck,
  normalizePromotionThresholds,
} from "../src/training/promotion-engine-workflow.js";

describe("promotion engine workflow", () => {
  it("normalizes threshold overrides", () => {
    expect(normalizePromotionThresholds()).toEqual(DEFAULT_PROMOTION_THRESHOLDS);
    expect(normalizePromotionThresholds({ shadowMinRatio: 0.9 })).toMatchObject({
      heldOutMinRatio: 0.9,
      shadowMinRatio: 0.9,
      regressionThreshold: 0.75,
    });
  });

  it("builds shadow promotion checks and guards missing incumbent baselines", async () => {
    const check = await buildShadowPromotionCheck({
      artifactId: "artifact-1",
      scenario: "grid_ctf",
      shadowExecutor: async () => ({
        score: 0.88,
        parseFailureRate: 0.01,
        validationFailureRate: 0.02,
        samplesRun: 10,
      }),
      run: { incumbentScore: 1.0, heldOutScore: 0.95 },
    });

    expect(check).toMatchObject({
      currentState: "shadow",
      incumbentScore: 1.0,
      heldOutScore: 0.95,
      shadowRunScore: 0.88,
    });

    await expect(buildShadowPromotionCheck({
      artifactId: "artifact-1",
      scenario: "grid_ctf",
      shadowExecutor: async () => ({
        score: 0.88,
        parseFailureRate: 0.01,
        validationFailureRate: 0.02,
        samplesRun: 10,
      }),
      run: { incumbentScore: 0, heldOutScore: 0.95 },
    })).rejects.toThrow("incumbentScore");
  });

  it("evaluates candidate promotion, shadow promotion, and regression rollback", () => {
    const candidateDecision = evaluatePromotionCheck({
      currentState: "candidate",
      heldOutScore: 0.92,
      incumbentScore: 0.9,
      parseFailureRate: 0,
      validationFailureRate: 0,
    }, DEFAULT_PROMOTION_THRESHOLDS);
    expect(candidateDecision).toMatchObject({ promote: true, rollback: false, targetState: "shadow" });

    const shadowDecision = evaluatePromotionCheck({
      currentState: "shadow",
      heldOutScore: 0.92,
      incumbentScore: 0.9,
      shadowRunScore: 0.88,
      parseFailureRate: 0.01,
      validationFailureRate: 0.02,
    }, DEFAULT_PROMOTION_THRESHOLDS);
    expect(shadowDecision).toMatchObject({ promote: true, rollback: false, targetState: "active" });

    const rollbackDecision = evaluatePromotionCheck({
      currentState: "active",
      heldOutScore: 0.6,
      incumbentScore: 0.9,
      shadowRunScore: 0.55,
      parseFailureRate: 0.2,
      validationFailureRate: 0.1,
    }, DEFAULT_PROMOTION_THRESHOLDS);
    expect(rollbackDecision).toMatchObject({ promote: false, rollback: true, targetState: "disabled" });
  });
});
