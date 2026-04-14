/**
 * Backpressure gates — simple and trend-aware (AC-346 Task 20).
 * Mirrors Python's harness/pipeline/gate.py and trend_gate.py.
 */

import { normalizeDecisionMetric } from "../analytics/number-utils.js";

export interface GateDecision {
  decision: "advance" | "retry" | "rollback";
  delta: number;
  threshold: number;
  reason: string;
  metadata: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Simple BackpressureGate
// ---------------------------------------------------------------------------

export class BackpressureGate {
  #minDelta: number;

  constructor(minDelta = 0.005) {
    this.#minDelta = minDelta;
  }

  evaluate(
    previousBest: number,
    currentBest: number,
    retryCount: number,
    maxRetries: number,
  ): GateDecision {
    const delta = normalizeDecisionMetric(currentBest - previousBest);

    if (delta >= this.#minDelta) {
      return {
        decision: "advance",
        delta,
        threshold: this.#minDelta,
        reason: "score improved",
        metadata: {},
      };
    }
    if (retryCount < maxRetries) {
      return {
        decision: "retry",
        delta,
        threshold: this.#minDelta,
        reason: "insufficient improvement; retry permitted",
        metadata: {},
      };
    }
    return {
      decision: "rollback",
      delta,
      threshold: this.#minDelta,
      reason: "insufficient improvement and retries exhausted",
      metadata: {},
    };
  }
}

// ---------------------------------------------------------------------------
// Trend-Aware Gate
// ---------------------------------------------------------------------------

export interface ScoreHistory {
  scores: number[];
  gateDecisions: string[];
}

export interface TrendAwareGateOpts {
  minDelta?: number;
  plateauWindow?: number;
  plateauRelaxationFactor?: number;
  consecutiveRollbackThreshold?: number;
}

const TREND_AWARE_GATE_DEFAULTS = {
  minDelta: 0.005,
  plateauWindow: 3,
  plateauRelaxationFactor: 0.5,
  consecutiveRollbackThreshold: 3,
};

export class TrendAwareGate {
  #minDelta: number;
  #plateauWindow: number;
  #plateauRelaxationFactor: number;
  #consecutiveRollbackThreshold: number;

  constructor(opts: TrendAwareGateOpts = {}) {
    const resolved = { ...TREND_AWARE_GATE_DEFAULTS, ...opts };
    this.#minDelta = resolved.minDelta;
    this.#plateauWindow = resolved.plateauWindow;
    this.#plateauRelaxationFactor = resolved.plateauRelaxationFactor;
    this.#consecutiveRollbackThreshold = resolved.consecutiveRollbackThreshold;
  }

  evaluate(
    previousBest: number,
    currentBest: number,
    retryCount: number,
    maxRetries: number,
    history?: ScoreHistory,
    customMetrics?: Record<string, number>,
  ): GateDecision {
    let effectiveDelta = this.#minDelta;

    // Plateau detection: low spread in recent scores
    if (history && history.scores.length > this.#plateauWindow) {
      const recent = history.scores.slice(-(this.#plateauWindow + 1), -1);
      const spread = Math.max(...recent) - Math.min(...recent);
      if (spread < this.#minDelta) {
        effectiveDelta = this.#minDelta * this.#plateauRelaxationFactor;
      }
    }

    // Consecutive rollback detection
    if (history && history.gateDecisions.length >= this.#consecutiveRollbackThreshold) {
      const recentDecisions = history.gateDecisions.slice(-this.#consecutiveRollbackThreshold);
      if (recentDecisions.every((d) => d === "rollback")) {
        effectiveDelta = this.#minDelta * this.#plateauRelaxationFactor;
      }
    }

    const delta = normalizeDecisionMetric(currentBest - previousBest);
    const metadata = customMetrics ?? {};

    if (delta >= effectiveDelta) {
      return { decision: "advance", delta, threshold: effectiveDelta, reason: "score improved", metadata };
    }
    if (retryCount < maxRetries) {
      return {
        decision: "retry",
        delta,
        threshold: effectiveDelta,
        reason: "insufficient improvement; retry permitted",
        metadata,
      };
    }
    return {
      decision: "rollback",
      delta,
      threshold: effectiveDelta,
      reason: "insufficient improvement and retries exhausted",
      metadata,
    };
  }
}
