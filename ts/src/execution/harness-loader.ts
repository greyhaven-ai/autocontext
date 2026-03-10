/**
 * HarnessLoader — type-safe harness validator registry for TypeScript.
 *
 * Unlike the Python version which dynamically loads .py files, the TS port
 * uses a programmatic validator registry. Validators are registered as
 * functions that conform to the ValidatorFn type.
 */

import { z } from "zod";

// ── Schemas ──────────────────────────────────────────────────────────────────

export const HarnessValidationResultSchema = z.object({
  passed: z.boolean(),
  errors: z.array(z.string()),
  validatorName: z.string().default(""),
});

export type HarnessValidationResult = z.infer<
  typeof HarnessValidationResultSchema
>;

export const HarnessSpecSchema = z.object({
  name: z.string(),
  code: z.string(),
  description: z.string().optional(),
});

export type HarnessSpec = z.infer<typeof HarnessSpecSchema>;

export const HarnessSpecsPayloadSchema = z.object({
  harness: z.array(HarnessSpecSchema),
});

export type HarnessSpecsPayload = z.infer<typeof HarnessSpecsPayloadSchema>;

// ── Marker parser ────────────────────────────────────────────────────────────

const HARNESS_START = "<!-- HARNESS_START -->";
const HARNESS_END = "<!-- HARNESS_END -->";

/**
 * Extract harness specs from architect output using markers.
 * Validates each entry individually so one bad entry doesn't reject all.
 */
export function parseArchitectHarnessSpecs(content: string): HarnessSpec[] {
  const startIdx = content.indexOf(HARNESS_START);
  if (startIdx === -1) return [];
  const endIdx = content.indexOf(HARNESS_END, startIdx);
  if (endIdx === -1) return [];

  const body = content.slice(startIdx + HARNESS_START.length, endIdx).trim();

  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return [];
  }

  if (typeof parsed !== "object" || parsed === null || !("harness" in parsed)) {
    return [];
  }

  const harness = (parsed as Record<string, unknown>).harness;
  if (!Array.isArray(harness)) return [];

  const valid: HarnessSpec[] = [];
  for (const item of harness) {
    const result = HarnessSpecSchema.safeParse(item);
    if (result.success) {
      valid.push(result.data);
    }
  }
  return valid;
}

// ── Validator registry ───────────────────────────────────────────────────────

export type ValidatorFn = (
  strategy: Record<string, unknown>,
  scenario: unknown,
) => { passed: boolean; errors: string[] };

export class HarnessLoader {
  private validators = new Map<string, ValidatorFn>();

  /** Register a named validator function. */
  register(name: string, fn: ValidatorFn): void {
    this.validators.set(name, fn);
  }

  /** Unregister a validator by name. */
  unregister(name: string): boolean {
    return this.validators.delete(name);
  }

  /** Run all registered validators against a strategy. */
  validateStrategy(
    strategy: Record<string, unknown>,
    scenario: unknown,
  ): HarnessValidationResult {
    if (this.validators.size === 0) {
      return { passed: true, errors: [], validatorName: "" };
    }

    const allErrors: string[] = [];
    for (const [name, fn] of this.validators) {
      try {
        const result = fn(strategy, scenario);
        if (!result.passed) {
          allErrors.push(...result.errors.map((e) => `[${name}] ${e}`));
        }
      } catch (err) {
        allErrors.push(
          `[${name}] validator threw: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    return {
      passed: allErrors.length === 0,
      errors: allErrors,
      validatorName: "",
    };
  }

  /** Get names of all registered validators. */
  get registeredNames(): string[] {
    return [...this.validators.keys()];
  }

  /** Check if a validator is registered. */
  has(name: string): boolean {
    return this.validators.has(name);
  }
}
