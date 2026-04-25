export interface SolveGenerationBudgetOpts {
  scenarioName: string;
  budgetSeconds?: number | null;
  nowMs?: () => number;
}

export class SolveGenerationBudget {
  readonly scenarioName: string;
  readonly budgetSeconds: number;
  #startedAtMs: number;
  #nowMs: () => number;

  constructor(opts: SolveGenerationBudgetOpts) {
    this.scenarioName = opts.scenarioName;
    this.budgetSeconds = normalizeBudgetSeconds(opts.budgetSeconds);
    this.#nowMs = opts.nowMs ?? (() => performance.now());
    this.#startedAtMs = this.#nowMs();
  }

  check(phase: string): void {
    if (this.budgetSeconds <= 0) {
      return;
    }
    const elapsedSeconds = Math.max(0, this.#nowMs() - this.#startedAtMs) / 1000;
    if (elapsedSeconds >= this.budgetSeconds) {
      throw new Error(
        `Solve generation time budget exceeded during ${phase} ` +
        `after ${elapsedSeconds.toFixed(2)}s for scenario '${this.scenarioName}' ` +
        `(budget ${this.budgetSeconds}s)`,
      );
    }
  }
}

function normalizeBudgetSeconds(value: number | null | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return 0;
  }
  return Math.floor(value);
}
