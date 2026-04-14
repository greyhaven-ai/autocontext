import type {
  PromotionCheck,
  PromotionDecision,
  PromotionThresholds,
  ShadowExecutor,
  ShadowRunOpts,
} from "./promotion-types.js";

export const DEFAULT_PROMOTION_THRESHOLDS: PromotionThresholds = {
  heldOutMinRatio: 0.90,
  shadowMinRatio: 0.85,
  maxParseFailureRate: 0.05,
  maxValidationFailureRate: 0.05,
  regressionThreshold: 0.75,
};

export function normalizePromotionThresholds(
  thresholds?: Partial<PromotionThresholds>,
): PromotionThresholds {
  return { ...DEFAULT_PROMOTION_THRESHOLDS, ...(thresholds ?? {}) };
}

export async function buildShadowPromotionCheck(opts: {
  artifactId: string;
  scenario: string;
  shadowExecutor?: ShadowExecutor;
  run: ShadowRunOpts;
}): Promise<PromotionCheck | null> {
  if (!opts.shadowExecutor) {
    return null;
  }
  if (opts.run.incumbentScore <= 0) {
    throw new Error("incumbentScore must be > 0 for shadow evaluation");
  }

  const result = await opts.shadowExecutor(opts.artifactId, opts.scenario);
  return {
    currentState: "shadow",
    heldOutScore: opts.run.heldOutScore,
    incumbentScore: opts.run.incumbentScore,
    shadowRunScore: result.score,
    parseFailureRate: result.parseFailureRate,
    validationFailureRate: result.validationFailureRate,
  };
}

export function evaluatePromotionCheck(
  check: PromotionCheck,
  thresholds: PromotionThresholds,
): PromotionDecision {
  const hasIncumbentBaseline = check.incumbentScore > 0;
  const heldOutRatio = hasIncumbentBaseline
    ? check.heldOutScore / check.incumbentScore
    : null;
  const shadowRatio = hasIncumbentBaseline && check.shadowRunScore != null
    ? check.shadowRunScore / check.incumbentScore
    : null;
  const comparisonRatio = shadowRatio ?? heldOutRatio;

  if ((check.currentState === "candidate" || check.currentState === "shadow") && !hasIncumbentBaseline) {
    return {
      promote: false,
      rollback: false,
      targetState: check.currentState,
      reasoning: "Incumbent score baseline is required before a candidate or shadow model can be promoted.",
    };
  }

  if (check.currentState === "active" || check.currentState === "shadow") {
    if (
      (comparisonRatio != null && comparisonRatio < thresholds.regressionThreshold)
      || check.parseFailureRate > thresholds.maxParseFailureRate * 2
    ) {
      return {
        promote: false,
        rollback: true,
        targetState: "disabled",
        reasoning: `Regression detected: comparison ratio ${(comparisonRatio ?? 0).toFixed(2)} (threshold ${thresholds.regressionThreshold}), parse failures ${(check.parseFailureRate * 100).toFixed(1)}%.`,
      };
    }
  }

  if (check.parseFailureRate > thresholds.maxParseFailureRate) {
    return {
      promote: false,
      rollback: false,
      targetState: check.currentState,
      reasoning: `parse failure rate ${(check.parseFailureRate * 100).toFixed(1)}% exceeds ${(thresholds.maxParseFailureRate * 100).toFixed(1)}% threshold.`,
    };
  }

  if (check.validationFailureRate > thresholds.maxValidationFailureRate) {
    return {
      promote: false,
      rollback: false,
      targetState: check.currentState,
      reasoning: `Validation failure rate ${(check.validationFailureRate * 100).toFixed(1)}% exceeds threshold.`,
    };
  }

  if (check.currentState === "candidate") {
    if ((heldOutRatio ?? 0) >= thresholds.heldOutMinRatio) {
      return {
        promote: true,
        rollback: false,
        targetState: "shadow",
        reasoning: `Held-out score ${check.heldOutScore.toFixed(2)} is ${((heldOutRatio ?? 0) * 100).toFixed(1)}% of incumbent ${check.incumbentScore.toFixed(2)} (threshold ${(thresholds.heldOutMinRatio * 100).toFixed(0)}%).`,
      };
    }
    return {
      promote: false,
      rollback: false,
      targetState: "candidate",
      reasoning: `Held-out score ${check.heldOutScore.toFixed(2)} is below ${(thresholds.heldOutMinRatio * 100).toFixed(0)}% of incumbent ${check.incumbentScore.toFixed(2)}.`,
    };
  }

  if (check.currentState === "shadow") {
    if (shadowRatio == null) {
      return {
        promote: false,
        rollback: false,
        targetState: "shadow",
        reasoning: "Shadow-run score is required before a shadow model can be promoted.",
      };
    }

    if (shadowRatio >= thresholds.shadowMinRatio && (heldOutRatio ?? 0) >= thresholds.heldOutMinRatio) {
      return {
        promote: true,
        rollback: false,
        targetState: "active",
        reasoning: `Shadow-run score ${check.shadowRunScore?.toFixed(2) ?? "N/A"} is ${(shadowRatio * 100).toFixed(1)}% of incumbent. Promoting to active.`,
      };
    }
    return {
      promote: false,
      rollback: false,
      targetState: "shadow",
      reasoning: "Shadow performance not yet sufficient for promotion.",
    };
  }

  return {
    promote: false,
    rollback: false,
    targetState: check.currentState,
    reasoning: "No state change needed.",
  };
}
