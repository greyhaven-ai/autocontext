import { describe, test, expect } from "vitest";
import fc from "fast-check";
import { deriveDatasetId } from "../../../../src/production-traces/contract/content-address.js";
import type { ContentHash } from "../../../../src/production-traces/contract/branded-ids.js";

function ch(hexChar: string): ContentHash {
  return ("sha256:" + hexChar.repeat(64)) as ContentHash;
}

describe("deriveDatasetId", () => {
  test("returns a 'ds_'-prefixed 26-char Crockford-base32 suffix", () => {
    const id = deriveDatasetId(ch("a"), ch("b"));
    expect(id).toMatch(/^ds_[0-9A-HJKMNP-TV-Z]{26}$/);
    expect(id).toHaveLength(29);
  });

  test("is deterministic — same inputs yield same output", () => {
    const a = deriveDatasetId(ch("a"), ch("b"));
    const b = deriveDatasetId(ch("a"), ch("b"));
    expect(a).toBe(b);
  });

  test("different inputs yield different outputs", () => {
    const a = deriveDatasetId(ch("a"), ch("b"));
    const b = deriveDatasetId(ch("a"), ch("c"));
    const c = deriveDatasetId(ch("c"), ch("b"));
    expect(a).not.toBe(b);
    expect(a).not.toBe(c);
    expect(b).not.toBe(c);
  });

  test("order of inputs matters (configHash vs inputTracesHash are not commutative)", () => {
    const a = deriveDatasetId(ch("a"), ch("b"));
    const b = deriveDatasetId(ch("b"), ch("a"));
    expect(a).not.toBe(b);
  });

  // P1 foundation property: determinism under all hash pairs.
  test("P1 foundation — determinism across fast-check generator of hash pairs", () => {
    const hashArb = fc.stringMatching(/^[0-9a-f]{64}$/).map((h) => ("sha256:" + h) as ContentHash);
    fc.assert(
      fc.property(hashArb, hashArb, (h1, h2) => {
        return deriveDatasetId(h1, h2) === deriveDatasetId(h1, h2);
      }),
      { numRuns: 100 },
    );
  });

  test("P1 foundation — inequality for distinct input pairs (collision extremely unlikely)", () => {
    const hashArb = fc.stringMatching(/^[0-9a-f]{64}$/).map((h) => ("sha256:" + h) as ContentHash);
    fc.assert(
      fc.property(hashArb, hashArb, hashArb, hashArb, (a1, a2, b1, b2) => {
        // Skip the trivial equal-input case.
        fc.pre(a1 !== b1 || a2 !== b2);
        return deriveDatasetId(a1, a2) !== deriveDatasetId(b1, b2);
      }),
      { numRuns: 100 },
    );
  });
});
