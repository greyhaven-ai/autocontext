import {
  type ModelRecord,
  ModelRegistry,
  PromotionEngine,
} from "./promotion.js";
import { readMetric } from "./training-metric-utils.js";
import type { TrainingConfig } from "./training-types.js";

export function registerPromotionCandidate(
  promotionRegistry: ModelRegistry,
  config: TrainingConfig,
  checkpointDir: string,
): { artifactId: string; record: ModelRecord | null } {
  const artifactId = promotionRegistry.register({
    scenario: config.scenario,
    family: config.family,
    backend: config.backend,
    checkpointDir,
    activationState: "candidate",
  });

  return {
    artifactId,
    record: promotionRegistry.get(artifactId),
  };
}

export function evaluatePromotionState(
  promotionRegistry: ModelRegistry,
  promotionEngine: PromotionEngine,
  artifactId: string,
  metrics: Record<string, number> | undefined,
): ModelRecord | null {
  const heldOutScore = readMetric(metrics, "heldOutScore", "held_out_score", "score");
  const incumbentScore = readMetric(metrics, "incumbentScore", "incumbent_score");

  if (heldOutScore != null && incumbentScore != null && incumbentScore > 0) {
    const decision = promotionEngine.evaluate({
      currentState: "candidate",
      heldOutScore,
      incumbentScore,
      parseFailureRate: readMetric(metrics, "parseFailureRate", "parse_failure_rate") ?? 0,
      validationFailureRate: readMetric(metrics, "validationFailureRate", "validation_failure_rate") ?? 0,
    });
    if (decision.targetState !== "candidate") {
      promotionRegistry.setState(artifactId, decision.targetState, {
        reason: decision.reasoning,
        evidence: metrics,
      });
    }
  }

  return promotionRegistry.get(artifactId);
}
