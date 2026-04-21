import { sha256HexSalted } from "../redaction/hash-primitives.js";
import type { SessionIdHash, UserIdHash } from "../contract/branded-ids.js";

/**
 * Customer-facing hashing helpers.
 *
 * DDD anchor: names mirror Python's ``hash_user_id`` / ``hash_session_id``.
 * Same algorithm (``sha256(salt + value)``), same output (64-char lowercase
 * hex, NO ``sha256:`` prefix — that prefix is specific to the redaction-
 * marker placeholder format inside a ProductionTrace document).
 *
 * DRY anchor: the SHA-256 primitive lives in
 * ``../redaction/hash-primitives.ts`` and is shared with the apply-at-export
 * redaction engine. Byte-identity with Python is enforced by
 * P-hashing-parity (100 runs) in the cross-runtime parity suite.
 */

// Re-export install-salt lifecycle so customers import everything from a
// single entry point: `import { ... } from "autoctx/production-traces"`.
export {
  loadInstallSalt,
  initializeInstallSalt,
  rotateInstallSalt,
  installSaltPath,
} from "../redaction/install-salt.js";

/**
 * Hash a user identifier with the install salt. Returns 64-char lowercase
 * hex — the value you can store in ``session.userIdHash``.
 *
 * Throws if ``salt`` is empty: hashing without a salt produces trivially
 * reversible output and must never silently proceed.
 */
export function hashUserId(userId: string, salt: string): UserIdHash {
  assertNonEmptySalt(salt);
  return sha256HexSalted(userId, salt) as UserIdHash;
}

/**
 * Hash a session identifier. Algorithmically identical to :func:`hashUserId`;
 * the distinct name documents intent and lets downstream processors filter
 * by the branded return type.
 */
export function hashSessionId(sessionId: string, salt: string): SessionIdHash {
  assertNonEmptySalt(salt);
  return sha256HexSalted(sessionId, salt) as SessionIdHash;
}

function assertNonEmptySalt(salt: string): void {
  if (typeof salt !== "string" || salt.length === 0) {
    throw new Error(
      "hashing salt must be a non-empty string — use loadInstallSalt() or initializeInstallSalt()",
    );
  }
}
