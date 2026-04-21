import { describe, test } from "vitest";
import fc from "fast-check";
import {
  hashUserId,
  hashSessionId,
} from "../../../src/production-traces/sdk/hashing.js";
import {
  callPythonHashUserId,
  callPythonHashSessionId,
  isPythonParityAvailable,
} from "../../_helpers/python-runner.js";

/**
 * P-hashing-parity — spec §5.3, 100 runs.
 *
 * Generates ``(userId, salt)`` pairs via fast-check, computes ``hashUserId``
 * in TypeScript, invokes Python ``hash_user_id`` via subprocess, asserts
 * byte-for-byte identity of the returned hex digests. Same for
 * ``hashSessionId``.
 *
 * Gated on ``isPythonParityAvailable()`` so contributors without the
 * ``autocontext`` Python package installed still have a green ``vitest run``
 * locally. CI exercises the assertion unconditionally.
 */

const parity = isPythonParityAvailable();
const maybeSuite = parity ? describe : describe.skip;

// Salt arbitrary — non-empty ASCII to stay safely inside JSON-over-stdin
// encoding. The actual production install-salt is 64 hex chars, but the
// primitive admits any non-empty salt, so we property-test the algorithm
// shape broadly.
const saltArb = fc.string({ minLength: 1, maxLength: 64 }).filter((s) => s.length > 0 && !s.includes("\u0000"));
const idArb = fc.string({ minLength: 1, maxLength: 64 }).filter((s) => s.length > 0 && !s.includes("\u0000"));

maybeSuite("P-hashing-parity (property, 100 runs)", () => {
  test("hashUserId matches Python hash_user_id byte-for-byte", () => {
    fc.assert(
      fc.property(idArb, saltArb, (userId, salt) => {
        const ts = hashUserId(userId, salt);
        const py = callPythonHashUserId(userId, salt);
        return ts === py;
      }),
      { numRuns: 100 },
    );
  }, 120_000);

  test("hashSessionId matches Python hash_session_id byte-for-byte", () => {
    fc.assert(
      fc.property(idArb, saltArb, (sessionId, salt) => {
        const ts = hashSessionId(sessionId, salt);
        const py = callPythonHashSessionId(sessionId, salt);
        return ts === py;
      }),
      { numRuns: 100 },
    );
  }, 120_000);
});
