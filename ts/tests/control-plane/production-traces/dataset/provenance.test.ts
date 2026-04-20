import { describe, test, expect } from "vitest";
import fc from "fast-check";
import {
  computeConfigHash,
  computeInputTracesHash,
  computeFileHash,
} from "../../../../src/production-traces/dataset/provenance.js";
import { traceIdOf } from "./_helpers/fixtures.js";
import { parseProductionTraceId } from "../../../../src/production-traces/contract/branded-ids.js";

// Deterministic JSON arbitrary — no `undefined` values so canonicalJsonStringify
// accepts everything the property generates.
const jsonValue: fc.Arbitrary<unknown> = fc.letrec((tie) => ({
  value: fc.oneof(
    { depthSize: "small" },
    fc.constant(null),
    fc.boolean(),
    fc.integer({ min: -1000, max: 1000 }),
    fc.string(),
    fc.array(tie("value"), { maxLength: 4 }),
    fc.dictionary(fc.string({ minLength: 1, maxLength: 6 }), tie("value"), { maxKeys: 4 }),
  ),
})).value;

describe("computeConfigHash", () => {
  test("same input → same hash", () => {
    const a = computeConfigHash({ name: "x", rules: [{ type: "gate" }] });
    const b = computeConfigHash({ name: "x", rules: [{ type: "gate" }] });
    expect(a).toBe(b);
  });

  test("different inputs → different hashes", () => {
    const a = computeConfigHash({ name: "x" });
    const b = computeConfigHash({ name: "y" });
    expect(a).not.toBe(b);
  });

  test("key order does not affect hash (canonical JSON)", () => {
    const a = computeConfigHash({ a: 1, b: 2 });
    const b = computeConfigHash({ b: 2, a: 1 });
    expect(a).toBe(b);
  });

  test("ContentHash format: sha256:<64-hex>", () => {
    const h = computeConfigHash({ k: "v" });
    expect(h).toMatch(/^sha256:[0-9a-f]{64}$/);
  });

  test("property: determinism across 100 runs", () => {
    fc.assert(
      fc.property(jsonValue, (obj) => {
        const a = computeConfigHash(obj);
        const b = computeConfigHash(obj);
        return a === b;
      }),
      { numRuns: 100 },
    );
  });
});

describe("computeInputTracesHash", () => {
  const id1 = traceIdOf("01K00000000000000000000001");
  const id2 = traceIdOf("01K00000000000000000000002");
  const id3 = traceIdOf("01K00000000000000000000003");

  test("input order does not affect hash (sorts first)", () => {
    const a = computeInputTracesHash([id1, id2, id3]);
    const b = computeInputTracesHash([id3, id1, id2]);
    expect(a).toBe(b);
  });

  test("different trace sets → different hashes", () => {
    const a = computeInputTracesHash([id1, id2]);
    const b = computeInputTracesHash([id1, id3]);
    expect(a).not.toBe(b);
  });

  test("empty input is stable", () => {
    const a = computeInputTracesHash([]);
    const b = computeInputTracesHash([]);
    expect(a).toBe(b);
    expect(a).toMatch(/^sha256:[0-9a-f]{64}$/);
  });
});

describe("computeFileHash", () => {
  test("SHA-256 of UTF-8 string matches buffer equivalent", () => {
    const s = "hello\n";
    expect(computeFileHash(s)).toBe(computeFileHash(Buffer.from(s, "utf-8")));
  });
});

// Silence unused-warning
void parseProductionTraceId;
