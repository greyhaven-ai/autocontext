/**
 * Customer-facing hashing helpers.
 *
 * DDD anchor: names mirror Python's ``hash_user_id`` / ``hash_session_id``.
 * Same algorithm (``sha256(salt + value)``), same output (64-char lowercase
 * hex, NO ``sha256:`` prefix — that prefix is specific to the redaction-
 * marker placeholder format inside a ProductionTrace document).
 *
 * Compatibility anchor: this module remains the umbrella SDK hashing surface.
 * Pure hashing lives in ``hashing-core.ts`` so @autocontext/core can claim it
 * without pulling in install-salt filesystem lifecycle.
 */

export { hashUserId, hashSessionId } from "./hashing-core.js";

// Re-export install-salt lifecycle so customers import everything from a
// single entry point: `import { ... } from "autoctx/production-traces"`.
export {
  loadInstallSalt,
  initializeInstallSalt,
  rotateInstallSalt,
  installSaltPath,
} from "../redaction/install-salt.js";
