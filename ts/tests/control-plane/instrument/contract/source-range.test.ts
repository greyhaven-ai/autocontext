/**
 * A2-I Layer 1 — SourceRange invariants.
 *
 * Spec §4.2: SourceRange has startByte, endByte, startLineCol, endLineCol. Byte
 * ranges monotonic (startByte <= endByte); line/col consistency invariants.
 *
 * These invariants are structural — enforced at construction by producers (scanner,
 * plugins). This test exercises a small helper + the type shape, and documents the
 * invariant as executable spec.
 */
import { describe, test, expect } from "vitest";
import type { SourceRange } from "../../../../src/control-plane/instrument/contract/plugin-interface.js";
import fc from "fast-check";

function isMonotonicByte(r: SourceRange): boolean {
  return r.startByte <= r.endByte;
}

function isMonotonicLineCol(r: SourceRange): boolean {
  if (r.startLineCol.line < r.endLineCol.line) return true;
  if (r.startLineCol.line > r.endLineCol.line) return false;
  return r.startLineCol.col <= r.endLineCol.col;
}

describe("SourceRange invariants", () => {
  test("startByte <= endByte (examples)", () => {
    const r: SourceRange = {
      startByte: 0,
      endByte: 0,
      startLineCol: { line: 1, col: 0 },
      endLineCol: { line: 1, col: 0 },
    };
    expect(isMonotonicByte(r)).toBe(true);
    expect(isMonotonicByte({ ...r, startByte: 10, endByte: 5 })).toBe(false);
  });

  test("line/col monotonic (examples)", () => {
    const r: SourceRange = {
      startByte: 0,
      endByte: 100,
      startLineCol: { line: 2, col: 4 },
      endLineCol: { line: 5, col: 0 },
    };
    expect(isMonotonicLineCol(r)).toBe(true);

    expect(
      isMonotonicLineCol({
        ...r,
        startLineCol: { line: 3, col: 2 },
        endLineCol: { line: 3, col: 10 },
      }),
    ).toBe(true);

    expect(
      isMonotonicLineCol({
        ...r,
        startLineCol: { line: 3, col: 10 },
        endLineCol: { line: 3, col: 2 },
      }),
    ).toBe(false);

    expect(
      isMonotonicLineCol({
        ...r,
        startLineCol: { line: 5, col: 0 },
        endLineCol: { line: 3, col: 10 },
      }),
    ).toBe(false);
  });

  test("property: well-formed SourceRange survives JSON round-trip preserving both monotonicities", () => {
    fc.assert(
      fc.property(
        fc.record({
          a: fc.integer({ min: 0, max: 1_000_000 }),
          b: fc.integer({ min: 0, max: 1_000_000 }),
          line1: fc.integer({ min: 1, max: 10_000 }),
          col1: fc.integer({ min: 0, max: 200 }),
          deltaLine: fc.integer({ min: 0, max: 100 }),
          col2: fc.integer({ min: 0, max: 200 }),
        }),
        (g) => {
          const [startByte, endByte] = g.a <= g.b ? [g.a, g.b] : [g.b, g.a];
          const startLine = g.line1;
          const endLine = startLine + g.deltaLine;
          // When lines equal, force col2 >= col1
          const startCol = g.col1;
          const endCol = g.deltaLine === 0 ? Math.max(g.col2, g.col1) : g.col2;

          const r: SourceRange = {
            startByte,
            endByte,
            startLineCol: { line: startLine, col: startCol },
            endLineCol: { line: endLine, col: endCol },
          };
          const roundTripped = JSON.parse(JSON.stringify(r)) as SourceRange;
          expect(isMonotonicByte(roundTripped)).toBe(true);
          expect(isMonotonicLineCol(roundTripped)).toBe(true);
          expect(roundTripped).toEqual(r);
        },
      ),
      { numRuns: 100 },
    );
  });
});
