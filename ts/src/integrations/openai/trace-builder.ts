/**
 * Helpers for assembling ProductionTrace objects from OpenAI requests/responses.
 *
 * Uses buildTrace from autoctx/production-traces as the validation-and-shape
 * source of truth. Redaction of error messages happens here. Mirror of Python
 * ``_trace_builder.py``.
 */

// trace-builder.ts will be implemented in Task 3.5
export function buildRequestSnapshot(_opts: {
  model: string;
  messages: unknown[];
  extraKwargs: Record<string, unknown>;
}): Record<string, unknown> {
  throw new Error("stub");
}
