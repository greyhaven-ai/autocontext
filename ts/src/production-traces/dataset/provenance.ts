/**
 * Provenance hash helpers for dataset generation (spec §8.4 manifest.provenance).
 *
 * Both hashes are deterministic SHA-256 digests over canonical JSON inputs:
 *
 * - `configHash`:      content-hash of the full build-dataset config (name,
 *   filter rules, cluster strategy, rubric config, etc.). Captures every knob
 *   that influences the output dataset so recomputing `deriveDatasetId` given
 *   the same config + traces produces the same ID.
 * - `inputTracesHash`: content-hash of the sorted list of source traceIds.
 *   Encoded as `\n`-joined text (stable, grep-friendly) and passed through the
 *   same SHA-256 → hex → "sha256:<hex>" wrapping as other ContentHash values.
 *
 * Same inputs → same output (property-tested as P1 foundation; see
 * `pipeline-idempotence.test.ts`).
 */
import { createHash } from "node:crypto";
import { canonicalJsonStringify } from "../../control-plane/contract/canonical-json.js";
import {
  parseContentHash,
  type ContentHash,
  type ProductionTraceId,
} from "../contract/branded-ids.js";

function toContentHash(hexDigest: string): ContentHash {
  const value = `sha256:${hexDigest}`;
  const parsed = parseContentHash(value);
  // SHA-256 hex is always 64 lowercase chars; parseContentHash is total here.
  // This invariant gives us the ContentHash brand without a runtime `as` cast
  // elsewhere. If the brand pattern ever changes, this is the single point of
  // adjustment. [budget: 0]
  if (parsed === null) {
    throw new Error(`provenance: SHA-256 digest did not match ContentHash pattern: ${value}`);
  }
  return parsed;
}

/**
 * Deterministic SHA-256 over canonical JSON encoding of an arbitrary config
 * value. The canonical encoder sorts object keys and rejects `undefined`, so
 * logically-equal configs hash identically regardless of input key order.
 */
export function computeConfigHash(config: unknown): ContentHash {
  const canonical = canonicalJsonStringify(config);
  const digest = createHash("sha256").update(canonical).digest("hex");
  return toContentHash(digest);
}

/**
 * Deterministic SHA-256 over the sorted list of trace IDs joined by newlines.
 * Sorting by UTF-16 code units matches `canonicalJsonStringify`'s ordering and
 * is stable across JS engines.
 */
export function computeInputTracesHash(
  traceIds: readonly ProductionTraceId[],
): ContentHash {
  const sorted = [...traceIds].sort();
  const text = sorted.join("\n");
  const digest = createHash("sha256").update(text).digest("hex");
  return toContentHash(digest);
}

/**
 * SHA-256 of a buffer's bytes wrapped as a ContentHash. Used for per-split
 * JSONL file hashing in the manifest.
 */
export function computeFileHash(bytes: Buffer | string): ContentHash {
  const buf = typeof bytes === "string" ? Buffer.from(bytes, "utf-8") : bytes;
  const digest = createHash("sha256").update(buf).digest("hex");
  return toContentHash(digest);
}
