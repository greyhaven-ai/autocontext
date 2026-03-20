/**
 * Backpressure gates — simple and trend-aware (AC-346 Task 20).
 * Mirrors Python's harness/pipeline/gate.py and trend_gate.py.
 */

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
  private minDelta: number;

  constructor(minDelta = 0.005) {
    this.minDelta = minDelta;
  }

  evaluate(
    previousBest: number,
    currentBest: number,
    retryCount: number,
    maxRetries: number,
  ): GateDecision {
    const delta = Number((currentBest - previousBest).toFixed(6));

    if (delta >= this.minDelta) {
      return {
        decision: "advance",
        delta,
        threshold: this.minDelta,
        reason: "score improved",
        metadata: {},
      };
    }
    if (retryCount < maxRetries) {
      return {
        decision: "retry",
        delta,
        threshold: this.minDelta,
        reason: "insufficient improvement; retry permitted",
        metadata: {},
      };
    }
    return {
      decision: "rollback",
      delta,
      threshold: this.minDelta,
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

export class TrendAwareGate {
  private simple: BackpressureGate;
  private minDelta: number;
  private plateauWindow: number;
  private plateauRelaxationFactor: number;
  private consecutiveRollbackThreshold: number;

  constructor(opts: TrendAwareGateOpts = {}) {
    this.minDelta = opts.minDelta ?? 0.005;
    this.plateauWindow = opts.plateauWindow ?? 3;
    this.plateauRelaxationFactor = opts.plateauRelaxationFactor ?? 0.5;
    this.consecutiveRollbackThreshold = opts.consecutiveRollbackThreshold ?? 3;
    this.simple = new BackpressureGate(this.minDelta);
  }

  evaluate(
    previousBest: number,
    currentBest: number,
    retryCount: number,
    maxRetries: number,
    history?: ScoreHistory,
    customMetrics?: Record<string, number>,
  ): GateDecision {
    let effectiveDelta = this.minDelta;

    // Plateau detection: low spread in recent scores
    if (history && history.scores.length > this.plateauWindow) {
      const recent = history.scores.slice(-(this.plateauWindow + 1), -1);
      const spread = Math.max(...recent) - Math.min(...recent);
      if (spread < this.minDelta) {
        effectiveDelta = this.minDelta * this.plateauRelaxationFactor;
      }
    }

    // Consecutive rollback detection
    if (history && history.gateDecisions.length >= this.consecutiveRollbackThreshold) {
      const recentDecisions = history.gateDecisions.slice(-this.consecutiveRollbackThreshold);
      if (recentDecisions.every((d) => d === "rollback")) {
        effectiveDelta = this.minDelta * this.plateauRelaxationFactor;
      }
    }

    const delta = Number((currentBest - previousBest).toFixed(6));
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
