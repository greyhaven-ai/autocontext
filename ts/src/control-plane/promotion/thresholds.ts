import type { PromotionThresholds } from "../contract/types.js";

export function defaultThresholds(): PromotionThresholds {
  return {
    qualityMinDelta: 0.05,
    costMaxRelativeIncrease: 0.2,          // +20% tokens
    latencyMaxRelativeIncrease: 0.2,       // +20% p95
    strongConfidenceMin: 0.9,
    moderateConfidenceMin: 0.7,
    strongQualityMultiplier: 2.0,
  };
}

/**
 * Confidence ∈ [0, 1] as a log10 function of the smallest sample size
 * across evaluated dimensions.
 *
 *   minSamples = 0     → 0
 *   minSamples = 1     → ~0.001 / log10(1001) ≈ 0.1
 *   minSamples = 100   → ~log10(101) / log10(1001) ≈ 0.67
 *   minSamples = 1000  → 1.0 (capped)
 *
 * Users can override by supplying a `confidenceFn` in PromotionThresholds
 * (wired in §6.3a of the spec; not yet exposed — v1 uses the default).
 */
export function computeConfidence(minSamples: number): number {
  if (!Number.isFinite(minSamples) || minSamples <= 0) return 0;
  const raw = Math.log10(minSamples + 1) / Math.log10(1001);
  if (raw >= 1) return 1;
  if (raw <= 0) return 0;
  return raw;
}
