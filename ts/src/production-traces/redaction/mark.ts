import { scanTextForSensitiveData } from "../../traces/redaction-detection-workflow.js";
import { BUILTIN_REDACTION_PATTERNS } from "../../traces/redaction-patterns.js";
import type { PatternDef } from "../../traces/redaction-types.js";
import type {
  ProductionTrace,
  RedactionMarker,
  RedactionReason,
} from "../contract/types.js";
import type { LoadedRedactionPolicy } from "./types.js";

/**
 * Mark-at-ingest redaction detection (spec §7.2).
 *
 * Runs pattern-based detection over the textual fields of a ProductionTrace
 * and appends `RedactionMarker`s to the trace's `redactions[]` array:
 *
 *   1. Client-provided markers (detectedBy === "client") are preserved
 *      unchanged and placed FIRST in the output array.
 *   2. Auto-detection (if `policy.autoDetect.enabled`) scans textual content
 *      across messages, tool calls (recursively), outcome.reasoning, and
 *      feedbackRefs[].comment. Matches map to one of the spec categories:
 *        - "pii-email"        → reason "pii-email"
 *        - "pii-phone"        → reason "pii-custom"  (no canonical enum value)
 *        - "pii-ssn"          → reason "pii-ssn"
 *        - "pii-credit-card"  → reason "pii-custom"
 *        - "secret-token"     → reason "secret-token"
 *   3. Custom patterns from policy run over the same scan targets.
 *   4. If `metadata.rawProviderPayload` is present, a blanket marker is
 *      added at `/metadata/rawProviderPayload` (NOT descended into).
 *
 * Duplicates with the same (path, category) are collapsed into one marker.
 * Client markers are never deduplicated against detection output — even if
 * a client and auto-detection marker target the same path+category, both
 * survive (client first).
 *
 * This function is synchronous and never throws. The input trace is NOT
 * mutated — a new trace object with an extended `redactions[]` array is
 * returned.
 *
 * ## Relationship to `traces/redaction-*`
 *
 * We *wrap* `scanTextForSensitiveData` (a pure text → Detection[] scanner)
 * and the `BUILTIN_REDACTION_PATTERNS` table as the substrate. We *do not*
 * reuse the higher-level `applyRedactionPolicy` or `RedactionPolicy` class
 * from `traces/redaction.ts` — those target flat text, not JSON-pointer-
 * based field rewriting, and the category vocabulary differs (the existing
 * code uses "email", "api_key", "credential", …; the production-trace spec
 * uses "pii-email", "secret-token", …). The mapping happens here.
 *
 * SSN and credit-card categories are not in the existing pattern table;
 * we add those locally below.
 */

type MarkerMeta = { reason: RedactionReason; category: string };

/**
 * Each pattern carries a `PatternDef.category` that is the KEY into
 * `categoryMeta` rather than the final marker category string. That
 * indirection avoids typing casts when converting from detector output to
 * RedactionMarker instances.
 */
type PatternBundle = {
  patterns: PatternDef[];
  categoryMeta: Map<string, MarkerMeta>;
};

function asMutableRecord(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function markRedactions(
  trace: ProductionTrace,
  policy: LoadedRedactionPolicy,
  nowIso?: string,
): ProductionTrace {
  const timestamp = nowIso ?? new Date().toISOString();

  // 1. Preserve client-provided markers in original order, first in the list.
  const clientMarkers: RedactionMarker[] = trace.redactions.filter(
    (m) => m.detectedBy === "client",
  );
  const nonClientOriginal: RedactionMarker[] = trace.redactions.filter(
    (m) => m.detectedBy !== "client",
  );

  // 2. Auto-detection: build patterns and scan every text field.
  const detected: RedactionMarker[] = [];
  if (policy.autoDetect.enabled) {
    const enabled = new Set(policy.autoDetect.categories);
    const bundle = buildAutoDetectBundle(enabled);
    scanTraceTextFields(trace, bundle, detected, timestamp);
  }

  // 3. Custom patterns.
  if (policy.customPatterns.length > 0) {
    const customBundle = buildCustomPatternBundle(policy);
    scanTraceTextFields(trace, customBundle, detected, timestamp);
  }

  // 4. Blanket rawProviderPayload marker.
  const rawBlanket: RedactionMarker[] = [];
  const meta = asMutableRecord(trace.metadata);
  if (meta !== null && "rawProviderPayload" in meta && meta.rawProviderPayload !== undefined) {
    rawBlanket.push({
      path: "/metadata/rawProviderPayload",
      reason: "pii-custom",
      category: "raw-provider-payload",
      detectedBy: "ingestion",
      detectedAt: timestamp,
    });
  }

  // 5. Deduplicate non-client markers by (path, category).
  const combinedAdded = [...nonClientOriginal, ...detected, ...rawBlanket];
  const deduped: RedactionMarker[] = [];
  const seen = new Set<string>();
  for (const marker of combinedAdded) {
    const key = `${marker.path}::${marker.category ?? marker.reason}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(marker);
  }

  return {
    ...trace,
    redactions: [...clientMarkers, ...deduped],
  };
}

// ---- Pattern-bundle construction ----

/**
 * Map an enabled spec-category set to a PatternBundle, reusing the existing
 * `BUILTIN_REDACTION_PATTERNS` where possible and adding SSN / credit-card
 * patterns locally (they are not in the existing table).
 *
 * Each pattern's `category` string is used as a key into `categoryMeta`
 * that records the final `(reason, category)` for the produced marker.
 */
function buildAutoDetectBundle(enabled: Set<string>): PatternBundle {
  const patterns: PatternDef[] = [];
  const categoryMeta = new Map<string, MarkerMeta>();

  // Pre-assign the spec category keys → marker metadata. These keys double
  // as pattern.category strings, so the scan loop can look up metadata
  // directly without any downstream casting.
  categoryMeta.set("pii-email", { reason: "pii-email", category: "pii-email" });
  categoryMeta.set("pii-phone", { reason: "pii-custom", category: "pii-phone" });
  categoryMeta.set("pii-ssn", { reason: "pii-ssn", category: "pii-ssn" });
  categoryMeta.set("pii-credit-card", { reason: "pii-custom", category: "pii-credit-card" });
  categoryMeta.set("secret-token", { reason: "secret-token", category: "secret-token" });

  if (enabled.has("pii-email")) {
    for (const p of BUILTIN_REDACTION_PATTERNS) {
      if (p.category === "email") patterns.push({ ...p, category: "pii-email" });
    }
  }
  if (enabled.has("pii-phone")) {
    for (const p of BUILTIN_REDACTION_PATTERNS) {
      if (p.category === "phone") patterns.push({ ...p, category: "pii-phone" });
    }
  }
  if (enabled.has("secret-token")) {
    for (const p of BUILTIN_REDACTION_PATTERNS) {
      if (p.category === "api_key" || p.category === "credential") {
        patterns.push({ ...p, category: "secret-token" });
      }
    }
  }
  if (enabled.has("pii-ssn")) {
    patterns.push({
      pattern: /\b\d{3}-\d{2}-\d{4}\b/g,
      category: "pii-ssn",
      label: "US SSN",
      confidence: 0.85,
    });
  }
  if (enabled.has("pii-credit-card")) {
    // 13-19 digit credit-card-shaped numbers separated by optional spaces
    // or dashes. Prioritize recall over precision; operators can override
    // via policy categoryOverrides for false positives.
    patterns.push({
      pattern: /\b(?:\d[ -]*?){13,19}\b/g,
      category: "pii-credit-card",
      label: "Credit card",
      confidence: 0.7,
    });
  }

  return { patterns, categoryMeta };
}

function buildCustomPatternBundle(policy: LoadedRedactionPolicy): PatternBundle {
  const patterns: PatternDef[] = [];
  const categoryMeta = new Map<string, MarkerMeta>();

  for (let i = 0; i < policy.customPatterns.length; i++) {
    const p = policy.customPatterns[i];
    let regex: RegExp;
    try {
      regex = new RegExp(p.regex, "g");
    } catch {
      // Malformed regex → skip pattern; never throw.
      continue;
    }
    // Use a fresh stable key so collisions across patterns never alias.
    const patternKey = `__custom__${i}`;
    categoryMeta.set(patternKey, { reason: p.reason, category: p.category });
    patterns.push({ pattern: regex, category: patternKey, label: p.name, confidence: 0.9 });
  }

  return { patterns, categoryMeta };
}

// ---- Scan traversal ----

function scanTraceTextFields(
  trace: ProductionTrace,
  bundle: PatternBundle,
  sink: RedactionMarker[],
  timestamp: string,
): void {
  if (bundle.patterns.length === 0) return;

  // messages[i].content
  for (let i = 0; i < trace.messages.length; i++) {
    scanText(trace.messages[i].content, `/messages/${i}/content`, bundle, sink, timestamp);
  }

  // toolCalls[j].args (recursive) and result (recursive)
  for (let j = 0; j < trace.toolCalls.length; j++) {
    const call = trace.toolCalls[j];
    scanValueRecursive(call.args, `/toolCalls/${j}/args`, bundle, sink, timestamp);
    if (call.result !== undefined) {
      scanValueRecursive(call.result, `/toolCalls/${j}/result`, bundle, sink, timestamp);
    }
  }

  // outcome.reasoning
  if (trace.outcome?.reasoning !== undefined) {
    scanText(trace.outcome.reasoning, "/outcome/reasoning", bundle, sink, timestamp);
  }

  // feedbackRefs[k].comment
  for (let k = 0; k < trace.feedbackRefs.length; k++) {
    const fb = trace.feedbackRefs[k];
    if (fb.comment !== undefined) {
      scanText(fb.comment, `/feedbackRefs/${k}/comment`, bundle, sink, timestamp);
    }
  }
}

function scanValueRecursive(
  value: unknown,
  path: string,
  bundle: PatternBundle,
  sink: RedactionMarker[],
  timestamp: string,
): void {
  if (typeof value === "string") {
    scanText(value, path, bundle, sink, timestamp);
    return;
  }
  if (value === null || value === undefined) return;
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      scanValueRecursive(value[i], `${path}/${i}`, bundle, sink, timestamp);
    }
    return;
  }
  const record = asMutableRecord(value);
  if (record !== null) {
    for (const [key, child] of Object.entries(record)) {
      scanValueRecursive(child, `${path}/${escapeJsonPointerToken(key)}`, bundle, sink, timestamp);
    }
  }
  // Numbers, booleans: nothing to scan.
}

function escapeJsonPointerToken(key: string): string {
  // Per RFC 6901 — ~ must be escaped before /.
  return key.replace(/~/g, "~0").replace(/\//g, "~1");
}

function scanText(
  text: string,
  path: string,
  bundle: PatternBundle,
  sink: RedactionMarker[],
  timestamp: string,
): void {
  if (text.length === 0) return;
  const detections = scanTextForSensitiveData(text, bundle.patterns, { dedup: true });
  for (const d of detections) {
    const meta = bundle.categoryMeta.get(d.category);
    if (meta === undefined) continue; // Defensive: unknown category string.
    sink.push({
      path,
      reason: meta.reason,
      category: meta.category,
      detectedBy: "ingestion",
      detectedAt: timestamp,
    });
  }
}
