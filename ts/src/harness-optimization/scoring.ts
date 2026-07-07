import type { PromotionScore } from "./contract/generated-types.js";

/**
 * Pure harness promotion scorer (AC-877).
 *
 * This is the TypeScript half of the AC-877 parity pair: the same weighted
 * formula lives here and in `autocontext/src/autocontext/harness_optimization/scoring.py`,
 * and a shared numeric fixture
 * (`fixtures/harness-optimization/promotion-score/score-cases.json`) proves both
 * languages compute identical scores.
 *
 * The formula is:
 *
 *   harness_promotion_score = dense_quality_score
 *                           + sparse_success_weight * sparse_success_rate
 *                           - token_cost_weight     * tokens_per_million
 *                           - error_weight          * error_rate
 *                           - variance_weight       * score_variance
 */

// Reuse the generated contract types so the scorer stays in lockstep with the schema.
export type Components = PromotionScore["components"];
export type Weights = PromotionScore["weights"];

/**
 * Compute the weighted harness promotion score for one candidate.
 *
 * Reward is the dense quality score plus the weighted sparse success rate; the
 * weighted token cost, error rate, and score variance are penalties.
 */
export function harnessPromotionScore(components: Components, weights: Weights): number {
  return (
    components.dense_quality_score +
    weights.sparse_success_weight * components.sparse_success_rate -
    weights.token_cost_weight * components.tokens_per_million -
    weights.error_weight * components.error_rate -
    weights.variance_weight * components.score_variance
  );
}

/**
 * Return whether the challenger beats the incumbent by more than `minMargin`.
 *
 * Both scores are recomputed here from their components under the SAME weights,
 * so the comparison can never read a stale stored score.
 */
export function beatsIncumbent(
  challengerComponents: Components,
  incumbentComponents: Components,
  weights: Weights,
  minMargin: number,
): boolean {
  const challengerScore = harnessPromotionScore(challengerComponents, weights);
  const incumbentScore = harnessPromotionScore(incumbentComponents, weights);
  return challengerScore - incumbentScore > minMargin;
}
