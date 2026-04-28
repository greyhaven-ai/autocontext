import { sha256HexSalted } from "../redaction/hash-primitives.js";
import type { SessionIdHash, UserIdHash } from "../contract/branded-ids.js";

/**
 * Customer-facing pure identifier hashing helpers.
 *
 * DDD anchor: names mirror Python's hash_user_id / hash_session_id. The
 * install-salt filesystem lifecycle is intentionally not owned here; callers
 * pass the salt explicitly so this module stays a deterministic SDK primitive.
 */

/**
 * Hash a user identifier with the install salt. Returns 64-char lowercase hex,
 * which can be stored in session.userIdHash.
 */
export function hashUserId(userId: string, salt: string): UserIdHash {
  assertNonEmptySalt(salt);
  return sha256HexSalted(userId, salt) as UserIdHash;
}

/**
 * Hash a session identifier. The algorithm matches hashUserId; the distinct
 * name documents intent and preserves the branded return type.
 */
export function hashSessionId(sessionId: string, salt: string): SessionIdHash {
  assertNonEmptySalt(salt);
  return sha256HexSalted(sessionId, salt) as SessionIdHash;
}

function assertNonEmptySalt(salt: string): void {
  if (typeof salt !== "string" || salt.length === 0) {
    throw new Error(
      "hashing salt must be a non-empty string; pass an initialized install salt explicitly",
    );
  }
}
