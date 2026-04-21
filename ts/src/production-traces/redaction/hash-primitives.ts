import { createHash } from "node:crypto";

/**
 * Shared SHA-256 primitives used by both the apply-at-export redaction engine
 * (``redaction/apply.ts``) and the customer-facing emit SDK (``sdk/hashing.ts``).
 *
 * DDD note: this module owns the ``sha256(salt + value)`` primitive. Higher-level
 * helpers compose it:
 *
 *   - ``redaction/apply.ts`` calls :func:`hashValue` and wraps the result in the
 *     ``sha256:<hex>`` placeholder convention used inside a trace document.
 *   - ``sdk/hashing.ts`` calls :func:`sha256HexSalted` directly and returns the
 *     raw lowercase hex to match Python's ``hash_user_id`` / ``hash_session_id``.
 *
 * DRY note: the hashing algorithm lives exactly once. Any behavioral change (for
 * example, algorithm migration) happens here.
 */

/**
 * Compute ``sha256(salt + value)`` as 64-char lowercase hex.
 *
 * Byte-identical to Python ``hashlib.sha256((salt + value).encode("utf-8")).hexdigest()``.
 */
export function sha256HexSalted(value: string, salt: string): string {
  return createHash("sha256").update(salt + value).digest("hex");
}

/**
 * Hash an arbitrary JSON-representable value with a salt, returning the raw
 * lowercase hex digest.
 *
 * Non-string inputs are stringified via ``JSON.stringify(current ?? null)`` —
 * this preserves the behavior of the private helper previously embedded in
 * ``redaction/apply.ts``. Callers that emit the redaction placeholder format
 * (``sha256:<hex>``) wrap the return value themselves.
 */
export function hashValue(current: unknown, salt: string): string {
  const text = typeof current === "string" ? current : JSON.stringify(current ?? null);
  return sha256HexSalted(text, salt);
}
