import type { ProductionTrace, TimingInfo, ValidationResult } from "./types.js";

/**
 * I3 — Timing sanity: endedAt must be >= startedAt, latencyMs must be >= 0.
 * Timestamps must be parseable as dates.
 */
export function validateTimingSanity(timing: TimingInfo): ValidationResult {
  const errors: string[] = [];
  const startMs = Date.parse(timing.startedAt);
  const endMs = Date.parse(timing.endedAt);
  if (Number.isNaN(startMs)) {
    errors.push(`timing.startedAt '${timing.startedAt}' is not a parseable date`);
  }
  if (Number.isNaN(endMs)) {
    errors.push(`timing.endedAt '${timing.endedAt}' is not a parseable date`);
  }
  if (!Number.isNaN(startMs) && !Number.isNaN(endMs) && endMs < startMs) {
    errors.push(`timing.endedAt (${timing.endedAt}) < startedAt (${timing.startedAt})`);
  }
  if (typeof timing.latencyMs !== "number" || timing.latencyMs < 0) {
    errors.push(`timing.latencyMs (${String(timing.latencyMs)}) must be >= 0`);
  }
  if (
    typeof timing.timeToFirstTokenMs === "number"
    && timing.timeToFirstTokenMs < 0
  ) {
    errors.push(`timing.timeToFirstTokenMs (${timing.timeToFirstTokenMs}) must be >= 0`);
  }
  return errors.length === 0 ? { valid: true } : { valid: false, errors };
}

/**
 * I5 (helper) — Validate a JSON Pointer per RFC 6901. Returns ValidationResult
 * indicating whether the pointer resolves to a real field in `obj`.
 *
 * Accepts:
 *   - ""              — whole document (root)
 *   - "/a/b/0/c"      — standard path
 *   - escaped tokens: ~0 -> "~", ~1 -> "/"
 *
 * Rejects:
 *   - non-empty pointers missing leading "/"
 *   - array indices that aren't numeric or are out of bounds
 *   - paths that traverse into a missing field
 */
export function validateJsonPointer(obj: unknown, pointer: string): ValidationResult {
  if (pointer === "") return { valid: true };
  if (!pointer.startsWith("/")) {
    return { valid: false, errors: [`json pointer '${pointer}' missing leading '/'`] };
  }
  // Split; first element is always empty (before the leading /) so drop it.
  const tokens = pointer.slice(1).split("/").map(unescapeToken);
  let current: unknown = obj;
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];
    if (Array.isArray(current)) {
      // Must be a non-negative integer in range.
      if (!/^(0|[1-9][0-9]*)$/.test(tok)) {
        return { valid: false, errors: [`json pointer '${pointer}': token '${tok}' not a valid array index`] };
      }
      const idx = Number(tok);
      if (idx >= current.length) {
        return { valid: false, errors: [`json pointer '${pointer}': index ${idx} out of bounds`] };
      }
      current = current[idx];
    } else if (current !== null && typeof current === "object") {
      const asRecord = current as Record<string, unknown>;
      if (!Object.prototype.hasOwnProperty.call(asRecord, tok)) {
        return { valid: false, errors: [`json pointer '${pointer}': field '${tok}' not found`] };
      }
      current = asRecord[tok];
    } else {
      return { valid: false, errors: [`json pointer '${pointer}': cannot traverse into scalar at token '${tok}'`] };
    }
  }
  return { valid: true };
}

function unescapeToken(t: string): string {
  // Per RFC 6901: ~1 must be decoded before ~0 to avoid collisions.
  return t.replace(/~1/g, "/").replace(/~0/g, "~");
}

/**
 * I5 — Every RedactionMarker path must resolve to a real field in the trace.
 */
export function validateRedactionPaths(trace: ProductionTrace): ValidationResult {
  const errors: string[] = [];
  for (const marker of trace.redactions) {
    const r = validateJsonPointer(trace, marker.path);
    if (!r.valid) {
      for (const e of r.errors) errors.push(`redactions[].path: ${e}`);
    }
  }
  return errors.length === 0 ? { valid: true } : { valid: false, errors };
}
