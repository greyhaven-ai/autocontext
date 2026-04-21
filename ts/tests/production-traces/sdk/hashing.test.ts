import { describe, test, expect } from "vitest";
import { createHash } from "node:crypto";
import {
  hashUserId,
  hashSessionId,
  loadInstallSalt,
  initializeInstallSalt,
  rotateInstallSalt,
} from "../../../src/production-traces/sdk/hashing.js";

describe("hashUserId", () => {
  const SALT = "a".repeat(64);

  test("returns 64-char lowercase hex", () => {
    const hash = hashUserId("alice@example.com", SALT);
    expect(typeof hash).toBe("string");
    expect(hash).toMatch(/^[0-9a-f]{64}$/);
  });

  test("matches raw sha256(salt + value) byte-for-byte (Python parity algorithm)", () => {
    const userId = "user-42";
    const expected = createHash("sha256").update(SALT + userId).digest("hex");
    expect(hashUserId(userId, SALT)).toBe(expected);
  });

  test("is deterministic — same inputs produce same output", () => {
    const h1 = hashUserId("alice", SALT);
    const h2 = hashUserId("alice", SALT);
    expect(h1).toBe(h2);
  });

  test("distinct inputs produce distinct hashes", () => {
    const h1 = hashUserId("alice", SALT);
    const h2 = hashUserId("bob", SALT);
    expect(h1).not.toBe(h2);
  });

  test("throws on empty salt", () => {
    expect(() => hashUserId("alice", "")).toThrow();
  });

  test("does NOT prepend 'sha256:' (that prefix is redaction-marker-specific)", () => {
    const hash = hashUserId("alice", SALT);
    expect(hash.startsWith("sha256:")).toBe(false);
  });
});

describe("hashSessionId", () => {
  const SALT = "b".repeat(64);

  test("returns 64-char lowercase hex", () => {
    const hash = hashSessionId("sess-123", SALT);
    expect(hash).toMatch(/^[0-9a-f]{64}$/);
  });

  test("matches raw sha256(salt + value)", () => {
    const sid = "sess-xyz";
    const expected = createHash("sha256").update(SALT + sid).digest("hex");
    expect(hashSessionId(sid, SALT)).toBe(expected);
  });

  test("same algorithm as hashUserId (semantic distinction only at call site)", () => {
    // By design hashUserId and hashSessionId share the `sha256(salt + value)`
    // algorithm — the distinct names express intent, not a different hash.
    const value = "shared";
    expect(hashSessionId(value, SALT)).toBe(hashUserId(value, SALT));
  });

  test("throws on empty salt", () => {
    expect(() => hashSessionId("s", "")).toThrow();
  });
});

describe("install-salt re-exports", () => {
  test("exports loadInstallSalt, initializeInstallSalt, rotateInstallSalt", () => {
    expect(typeof loadInstallSalt).toBe("function");
    expect(typeof initializeInstallSalt).toBe("function");
    expect(typeof rotateInstallSalt).toBe("function");
  });
});
