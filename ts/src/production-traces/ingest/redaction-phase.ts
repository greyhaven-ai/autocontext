import type { ProductionTrace } from "../contract/types.js";

/* ============================================================================
 *  LAYER 3 PLACEHOLDER — PASSTHROUGH ONLY.
 *  ==========================================================================
 *  This module exposes the final Layer 4 shape so the ingest scan workflow
 *  already routes traces through the mark-at-ingest step. In Layer 3 the
 *  implementation is deliberately trivial: the input trace is returned
 *  unchanged (structural equality preserved).
 *
 *  TODO(Layer 4): wire into ts/src/traces/redaction-* to apply default
 *  auto-detection patterns (pii-email, pii-phone, pii-ssn, pii-credit-card,
 *  secret-token) and the blanket `rawProviderPayload` marker. See spec
 *  §7.2 ("Mark-at-ingest — always on"). The function signature here is the
 *  contract Layer 4 will implement against — do NOT change the signature
 *  when Layer 4 lands; swap the body only.
 * ========================================================================= */

/**
 * Run mark-at-ingest redaction detection against `trace`. In Layer 3 this is
 * a pure passthrough; Layer 4 will replace the body to invoke the detection
 * workflow and return a trace whose `redactions[]` includes auto-detected
 * markers.
 */
export function markRedactions(trace: ProductionTrace): ProductionTrace {
  return trace;
}
