import { validateProductionTrace } from "../contract/validators.js";
import { validateTimingSanity, validateRedactionPaths } from "../contract/invariants.js";
import type { ProductionTrace } from "../contract/types.js";

/**
 * Per-line validation result. Success carries the fully-validated trace.
 * Failure carries a human-readable reason and — when we made it far enough
 * to see the `traceId` — the id we attempted. Never throws.
 */
export type IngestLineResult =
  | { readonly ok: true; readonly trace: ProductionTrace }
  | { readonly ok: false; readonly reason: string; readonly attemptedTraceId?: string };

/**
 * Validate one line from an `incoming/*.jsonl` batch. The pipeline is:
 *   1. JSON.parse  — tolerate; malformed → reason: json
 *   2. validateProductionTrace (AJV)  — schema failure → reason: schema
 *   3. validateTimingSanity  — I3 invariant
 *   4. validateRedactionPaths  — I5 invariant
 *
 * Purely functional and allocation-light. Callers accumulate results per
 * batch and decide what to move to `ingested/` vs. `failed/`.
 */
export function validateIngestedLine(rawLine: string): IngestLineResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawLine);
  } catch (err) {
    return { ok: false, reason: `json parse error: ${(err as Error).message}` };
  }

  // Try to extract the traceId early so rejection paths can report it.
  let attemptedTraceId: string | undefined;
  if (parsed !== null && typeof parsed === "object") {
    const cand = (parsed as { traceId?: unknown }).traceId;
    if (typeof cand === "string") attemptedTraceId = cand;
  }

  const schemaResult = validateProductionTrace(parsed);
  if (!schemaResult.valid) {
    return {
      ok: false,
      reason: `schema: ${schemaResult.errors.join("; ")}`,
      ...(attemptedTraceId !== undefined ? { attemptedTraceId } : {}),
    };
  }

  // After validateProductionTrace.valid === true, the input is a ProductionTrace.
  const trace = parsed as ProductionTrace;

  const timing = validateTimingSanity(trace.timing);
  if (!timing.valid) {
    return {
      ok: false,
      reason: `timing: ${timing.errors.join("; ")}`,
      attemptedTraceId: trace.traceId,
    };
  }

  const redactions = validateRedactionPaths(trace);
  if (!redactions.valid) {
    return {
      ok: false,
      reason: `redactions: ${redactions.errors.join("; ")}`,
      attemptedTraceId: trace.traceId,
    };
  }

  return { ok: true, trace };
}
