/**
 * Stagnation detection — detect score plateaus and consecutive rollbacks (AC-349 Task 36).
 * Mirrors Python's autocontext/knowledge/stagnation.py.
 */

export interface StagnationReport {
  isStagnated: boolean;
  trigger: "none" | "consecutive_rollbacks" | "score_plateau";
  detail: string;
}

export interface StagnationDetectorOpts {
  rollbackThreshold?: number;
  plateauWindow?: number;
  plateauEpsilon?: number;
}

export class StagnationDetector {
  #rollbackThreshold: number;
  #plateauWindow: number;
  #plateauEpsilon: number;

  constructor(opts: StagnationDetectorOpts = {}) {
    this.#rollbackThreshold = opts.rollbackThreshold ?? 5;
    this.#plateauWindow = opts.plateauWindow ?? 5;
    this.#plateauEpsilon = opts.plateauEpsilon ?? 0.01;
  }

  detect(gateHistory: string[], scoreHistory: number[]): StagnationReport {
    // Count trailing rollbacks (ignoring retries)
    let consecutiveRollbacks = 0;
    for (let i = gateHistory.length - 1; i >= 0; i--) {
      if (gateHistory[i] === "rollback") {
        consecutiveRollbacks++;
      } else {
        break;
      }
    }

    if (consecutiveRollbacks >= this.#rollbackThreshold) {
      return {
        isStagnated: true,
        trigger: "consecutive_rollbacks",
        detail: `${consecutiveRollbacks} consecutive rollbacks`,
      };
    }

    // Check score plateau
    if (scoreHistory.length >= this.#plateauWindow) {
      const window = scoreHistory.slice(-this.#plateauWindow);
      const mean = window.reduce((a, b) => a + b, 0) / window.length;
      const variance = window.reduce((sum, s) => sum + (s - mean) ** 2, 0) / window.length;
      const stddev = Math.sqrt(variance);
      if (stddev < this.#plateauEpsilon) {
        return {
          isStagnated: true,
          trigger: "score_plateau",
          detail:
            `score stddev ${stddev.toFixed(6)} < epsilon ${this.#plateauEpsilon} ` +
            `over last ${this.#plateauWindow} gens`,
        };
      }
    }

    return { isStagnated: false, trigger: "none", detail: "" };
  }
}
