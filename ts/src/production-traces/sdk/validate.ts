import type { ProductionTrace } from "../contract/types.js";
import { validateProductionTrace as validateViaAjv } from "../contract/validators.js";

/**
 * Customer-facing validation surface for ``ProductionTrace`` documents.
 *
 * DRY anchor: both entry points delegate to the AJV validator shipped in
 * ``production-traces/contract/validators.ts``. The JSON Schemas are the
 * single source of truth; this module is a thin ergonomics layer.
 *
 * DDD anchor: names mirror Foundation A Layer 6's Python SDK —
 * ``validate_production_trace`` (throws) + ``validate_production_trace_dict``
 * (non-throwing). camelCase only translates the naming convention; semantics
 * match Python exactly.
 */

/**
 * Structured validation failure. Carries a summary message plus the list of
 * per-field errors that AJV reported. Enterprise integrations typically log
 * ``fieldErrors`` directly for operator visibility.
 */
export class ValidationError extends Error {
  readonly fieldErrors: readonly string[];

  constructor(message: string, fieldErrors: readonly string[]) {
    super(message);
    this.name = "ValidationError";
    this.fieldErrors = fieldErrors;
    // Set prototype so `instanceof` works after transpilation targeting ES5+
    // environments. Node 18+ / modern runtimes honor this pattern.
    Object.setPrototypeOf(this, ValidationError.prototype);
  }
}

/**
 * Ergonomic result shape for the non-throwing validator. ``errors`` is always
 * present (empty array on success) so call sites never need a defined-check.
 */
export interface ValidateResult {
  readonly valid: boolean;
  readonly errors: readonly string[];
}

/**
 * Validate and return a ``ProductionTrace`` document. On failure raises
 * :class:`ValidationError` carrying the structured AJV errors in
 * ``fieldErrors``.
 */
export function validateProductionTrace(input: unknown): ProductionTrace {
  const result = validateViaAjv(input);
  if (result.valid) {
    return input as ProductionTrace;
  }
  const errors = result.errors;
  const message = errors.length === 1
    ? `ProductionTrace validation failed: ${errors[0]}`
    : `ProductionTrace validation failed: ${errors.length} errors (first: ${errors[0]})`;
  throw new ValidationError(message, errors);
}

/**
 * Non-raising variant — returns ``{ valid, errors }``. Mirrors Python's
 * ``validate_production_trace_dict`` for customers who prefer to branch on a
 * flag rather than try/catch.
 */
export function validateProductionTraceDict(input: unknown): ValidateResult {
  const result = validateViaAjv(input);
  if (result.valid) return { valid: true, errors: [] };
  return { valid: false, errors: result.errors };
}
