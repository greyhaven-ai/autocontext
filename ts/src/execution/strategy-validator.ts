/**
 * StrategyValidator — pre-validates strategies via self-play dry-run.
 * Port of autocontext/src/autocontext/execution/strategy_validator.py (TypeScript port)
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Schemas and types
// ---------------------------------------------------------------------------

export const ValidationResultSchema = z.object({
  passed: z.boolean(),
  errors: z.array(z.string()).default([]),
  matchSummary: z.string().default(""),
});

export type ValidationResult = z.infer<typeof ValidationResultSchema>;

/** Minimal match result returned by a dry-run self-play execution. */
export interface MatchResult {
  score: number;
  summary: string;
  validationErrors?: string[];
}

/** Signature of the executeMatch callback. */
export type ExecuteMatchFn = (
  strategy: Record<string, unknown>,
  seed: number,
) => Promise<MatchResult>;

/** Constructor options for StrategyValidator. */
export interface StrategyValidatorOpts {
  /** Function that executes a self-play match with the strategy. */
  executeMatch: ExecuteMatchFn;
  /** Max revision attempts on failure (default: 2). */
  maxRetries?: number;
}

// ---------------------------------------------------------------------------
// StrategyValidator
// ---------------------------------------------------------------------------

export class StrategyValidator {
  private readonly executeMatch: ExecuteMatchFn;
  private readonly maxRetries: number;

  constructor(opts: StrategyValidatorOpts) {
    this.executeMatch = opts.executeMatch;
    this.maxRetries = opts.maxRetries ?? 2;
  }

  /**
   * Validate a strategy via self-play dry-run.
   * Code strategies (with __code__ key) skip dry-run and always pass.
   */
  async validate(strategy: Record<string, unknown>): Promise<ValidationResult> {
    // Code strategies bypass dry-run validation
    if ("__code__" in strategy) {
      return { passed: true, errors: [], matchSummary: "" };
    }

    try {
      const result = await this.executeMatch(strategy, 0);
      if (result.validationErrors && result.validationErrors.length > 0) {
        return {
          passed: false,
          errors: result.validationErrors,
          matchSummary: result.summary,
        };
      }
      return { passed: true, errors: [], matchSummary: result.summary };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return { passed: false, errors: [message], matchSummary: "" };
    }
  }

  /**
   * Format a human-readable revision prompt from a validation failure.
   * The prompt describes what went wrong and includes the original strategy.
   */
  formatRevisionPrompt(
    result: ValidationResult,
    originalStrategy: Record<string, unknown>,
  ): string {
    const errBlock = result.errors.map((e, i) => `${i + 1}. ${e}`).join("\n");
    const stratBlock = JSON.stringify(originalStrategy, null, 2);
    return [
      "Your strategy failed pre-validation with the following errors:",
      "",
      errBlock,
      "",
      "Original strategy:",
      "```json",
      stratBlock,
      "```",
      "",
      "Please fix the issues and provide a corrected strategy.",
    ].join("\n");
  }

  /**
   * Run validation with automatic retries on failure.
   *
   * On each failed attempt (up to maxRetries), the revise callback is called
   * with a prompt describing the errors, and the returned strategy is used for
   * the next attempt.
   *
   * Returns the final ValidationResult, the strategy used in the last attempt,
   * and the total number of attempts made.
   */
  async validateWithRetries(
    strategy: Record<string, unknown>,
    revise: (prompt: string) => Promise<Record<string, unknown>>,
  ): Promise<{
    result: ValidationResult;
    finalStrategy: Record<string, unknown>;
    attempts: number;
  }> {
    let current = strategy;

    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      const result = await this.validate(current);

      if (result.passed) {
        return { result, finalStrategy: current, attempts: attempt + 1 };
      }

      if (attempt < this.maxRetries) {
        const prompt = this.formatRevisionPrompt(result, current);
        current = await revise(prompt);
      }
    }

    // All retries exhausted — run one final validation and return failure
    const finalResult = await this.validate(current);
    return {
      result: finalResult,
      finalStrategy: current,
      attempts: this.maxRetries + 1,
    };
  }
}
