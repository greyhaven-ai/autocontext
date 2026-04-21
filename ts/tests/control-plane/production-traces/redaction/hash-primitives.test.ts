import { describe, test, expect } from "vitest";
import { createHash } from "node:crypto";
import {
  hashValue,
  sha256HexSalted,
} from "../../../../src/production-traces/redaction/hash-primitives.js";

/**
 * Behavioral pin for the extracted ``hashValue`` primitive. The value was
 * previously private inside ``redaction/apply.ts``. If the pinned hex below
 * changes, any caller depending on ``sha256(salt + value)`` semantics — which
 * includes both the redaction engine's ``sha256:<hex>`` placeholder format AND
 * the customer-facing ``hashUserId`` / ``hashSessionId`` SDK — will silently
 * drift from byte-identity with the Python reference implementation.
 */
describe("hashValue (pinned behavior)", () => {
  test("hashValue('test', 'salt') produces the known sha256 hex digest", () => {
    // Independently computed: sha256("salttest") hex — pinned to detect any
    // algorithmic drift (e.g. accidental change to salt/value ordering).
    const expected = "1bc1a361f17092bc7af4b2f82bf9194ea9ee2ca49eb2e53e39f555bc1eeaed74";
    const got = hashValue("test", "salt");
    expect(got).toBe(expected);
  });

  test("sha256HexSalted matches Node's createHash directly", () => {
    const salt = "a".repeat(64);
    const value = "alice@example.com";
    const expected = createHash("sha256").update(salt + value).digest("hex");
    expect(sha256HexSalted(value, salt)).toBe(expected);
  });

  test("hashValue on non-string inputs stringifies via JSON.stringify(x ?? null)", () => {
    const salt = "s";
    expect(hashValue(null, salt)).toBe(
      createHash("sha256").update(salt + "null").digest("hex"),
    );
    expect(hashValue(undefined, salt)).toBe(
      createHash("sha256").update(salt + "null").digest("hex"),
    );
    expect(hashValue({ a: 1 }, salt)).toBe(
      createHash("sha256").update(salt + JSON.stringify({ a: 1 })).digest("hex"),
    );
    expect(hashValue(42, salt)).toBe(
      createHash("sha256").update(salt + "42").digest("hex"),
    );
  });

  test("empty salt is tolerated at the primitive layer (SDK enforces non-empty)", () => {
    // The primitive does not validate the salt — salt policy lives with callers
    // so apply.ts can still pass an empty salt when install-salt is unset.
    const got = hashValue("x", "");
    expect(got).toBe(createHash("sha256").update("x").digest("hex"));
  });
});
