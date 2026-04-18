import { describe, test, expect } from "vitest";
import {
  canonicalJsonStringify,
} from "../../../src/control-plane/contract/canonical-json.js";

describe("canonicalJsonStringify", () => {
  test("serializes primitives", () => {
    expect(canonicalJsonStringify(null)).toBe("null");
    expect(canonicalJsonStringify(true)).toBe("true");
    expect(canonicalJsonStringify(false)).toBe("false");
    expect(canonicalJsonStringify(0)).toBe("0");
    expect(canonicalJsonStringify(42)).toBe("42");
    expect(canonicalJsonStringify(-7)).toBe("-7");
    expect(canonicalJsonStringify("hello")).toBe('"hello"');
  });

  test("sorts object keys by UTF-16 code units", () => {
    expect(canonicalJsonStringify({ b: 2, a: 1 })).toBe('{"a":1,"b":2}');
    expect(canonicalJsonStringify({ z: 0, aa: 0, a: 0 })).toBe('{"a":0,"aa":0,"z":0}');
  });

  test("sorts keys recursively", () => {
    const input = { z: { y: 2, x: 1 }, a: [{ d: 4, c: 3 }] };
    expect(canonicalJsonStringify(input)).toBe('{"a":[{"c":3,"d":4}],"z":{"x":1,"y":2}}');
  });

  test("preserves array element order", () => {
    expect(canonicalJsonStringify([3, 1, 2])).toBe("[3,1,2]");
    expect(canonicalJsonStringify([{ b: 1 }, { a: 2 }])).toBe('[{"b":1},{"a":2}]');
  });

  test("uses no insignificant whitespace", () => {
    const result = canonicalJsonStringify({ a: [1, 2], b: { c: 3 } });
    expect(result).toBe('{"a":[1,2],"b":{"c":3}}');
    expect(result).not.toContain(" ");
    expect(result).not.toContain("\n");
    expect(result).not.toContain("\t");
  });

  test("escapes string content per RFC 8259", () => {
    expect(canonicalJsonStringify("quote \" here")).toBe('"quote \\" here"');
    expect(canonicalJsonStringify("back\\slash")).toBe('"back\\\\slash"');
    expect(canonicalJsonStringify("line\nfeed")).toBe('"line\\nfeed"');
    expect(canonicalJsonStringify("tab\there")).toBe('"tab\\there"');
  });

  test("unicode keys sort by code-unit order (not by ASCII-folded)", () => {
    // 'ä' (U+00E4) > 'z' (U+007A) in code units
    const input = { ä: 1, z: 2 };
    expect(canonicalJsonStringify(input)).toBe('{"z":2,"ä":1}');
  });

  test("same logical content, different input key orders → byte-identical output", () => {
    const a = { x: 1, y: [{ p: 1, q: 2 }, { r: 3 }] };
    const b = { y: [{ q: 2, p: 1 }, { r: 3 }], x: 1 };
    expect(canonicalJsonStringify(a)).toBe(canonicalJsonStringify(b));
  });

  test("rejects non-finite numbers (JCS forbids NaN/Infinity)", () => {
    expect(() => canonicalJsonStringify(NaN)).toThrow(/NaN|finite/i);
    expect(() => canonicalJsonStringify(Infinity)).toThrow(/Infinity|finite/i);
    expect(() => canonicalJsonStringify(-Infinity)).toThrow(/Infinity|finite/i);
    expect(() => canonicalJsonStringify({ a: NaN })).toThrow(/NaN|finite/i);
  });

  test("rejects functions and undefined (unrepresentable in JSON)", () => {
    expect(() => canonicalJsonStringify(undefined as unknown as null)).toThrow();
    expect(() => canonicalJsonStringify({ a: undefined })).toThrow();
    // eslint-disable-next-line @typescript-eslint/no-empty-function
    expect(() => canonicalJsonStringify((() => {}) as unknown as null)).toThrow();
  });

  test("omits object keys with undefined values (matches JSON.stringify semantics would drop them; we reject explicitly)", () => {
    // We REJECT explicit undefined values to avoid silent content drops that would break signatures.
    expect(() => canonicalJsonStringify({ a: 1, b: undefined })).toThrow();
  });
});
