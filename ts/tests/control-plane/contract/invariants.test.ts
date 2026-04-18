import { describe, test, expect } from "vitest";
import {
  validateLineageNoCycles,
  validateAppendOnly,
  computeTreeHash,
  type TreeFile,
} from "../../../src/control-plane/contract/invariants.js";
import type { ArtifactId, PromotionEvent } from "../../../src/control-plane/contract/types.js";

const id = (s: string) => s as ArtifactId;

describe("validateLineageNoCycles (I4)", () => {
  test("returns valid for empty parent list", () => {
    const result = validateLineageNoCycles(id("01KPEYB3BRQWK2WSHK9E93N6NP"), [], (_x) => null);
    expect(result.valid).toBe(true);
  });

  test("returns valid for a non-cyclic chain A→B→C", () => {
    const A = id("01KPEYB3BRQWK2WSHK9E93N6NP");
    const B = id("01KPEYB3BRYCQ6J235VBR7WBY8");
    const C = id("01KPEYB3BQNFDEYRS8KH538PF5");
    // A has no parents; B's parent is A; C's parent is B.
    const lookup = (x: ArtifactId): readonly ArtifactId[] | null => {
      if (x === A) return [];
      if (x === B) return [A];
      return null;
    };
    const result = validateLineageNoCycles(C, [B], lookup);
    expect(result.valid).toBe(true);
  });

  test("rejects a direct self-reference", () => {
    const A = id("01KPEYB3BRQWK2WSHK9E93N6NP");
    const result = validateLineageNoCycles(A, [A], () => []);
    expect(result.valid).toBe(false);
  });

  test("rejects a cycle A → B → A", () => {
    const A = id("01KPEYB3BRQWK2WSHK9E93N6NP");
    const B = id("01KPEYB3BRYCQ6J235VBR7WBY8");
    // B's parent is A; new artifact A claims parent B. Adding A(parents=[B]) closes A→B→A.
    const lookup = (x: ArtifactId): readonly ArtifactId[] | null => (x === B ? [A] : null);
    const result = validateLineageNoCycles(A, [B], lookup);
    expect(result.valid).toBe(false);
  });
});

describe("validateAppendOnly (I3)", () => {
  const event = (n: number): PromotionEvent => ({
    from: "candidate",
    to: "shadow",
    reason: `r${n}`,
    timestamp: `2026-04-17T12:0${n}:00.000Z`,
  });

  test("returns valid when next is a proper extension of prev", () => {
    const prev = [event(1), event(2)];
    const next = [event(1), event(2), event(3)];
    expect(validateAppendOnly(prev, next).valid).toBe(true);
  });

  test("returns valid when prev and next are identical", () => {
    const prev = [event(1), event(2)];
    expect(validateAppendOnly(prev, prev).valid).toBe(true);
  });

  test("rejects mutation of an existing event", () => {
    const prev = [event(1), event(2)];
    const mutated = [{ ...event(1), reason: "changed" }, event(2)];
    expect(validateAppendOnly(prev, mutated).valid).toBe(false);
  });

  test("rejects removal (shorter next)", () => {
    const prev = [event(1), event(2)];
    const next = [event(1)];
    expect(validateAppendOnly(prev, next).valid).toBe(false);
  });

  test("rejects reordering", () => {
    const prev = [event(1), event(2)];
    const next = [event(2), event(1)];
    expect(validateAppendOnly(prev, next).valid).toBe(false);
  });
});

describe("computeTreeHash (content addressing)", () => {
  const file = (path: string, content: string): TreeFile => ({
    path,
    content: new TextEncoder().encode(content),
  });

  test("empty tree yields a defined hash", () => {
    const h = computeTreeHash([]);
    expect(h).toMatch(/^sha256:[0-9a-f]{64}$/);
  });

  test("same files produce same hash regardless of input order", () => {
    const files = [file("a.txt", "A"), file("b.txt", "B")];
    const reversed = [...files].reverse();
    expect(computeTreeHash(files)).toBe(computeTreeHash(reversed));
  });

  test("different content yields different hashes", () => {
    const h1 = computeTreeHash([file("a.txt", "A")]);
    const h2 = computeTreeHash([file("a.txt", "B")]);
    expect(h1).not.toBe(h2);
  });

  test("different paths with same content yield different hashes", () => {
    const h1 = computeTreeHash([file("a.txt", "X")]);
    const h2 = computeTreeHash([file("b.txt", "X")]);
    expect(h1).not.toBe(h2);
  });

  test("binary content (non-UTF8) is handled", () => {
    const bin: TreeFile = { path: "raw.bin", content: new Uint8Array([0, 1, 2, 255]) };
    const h = computeTreeHash([bin]);
    expect(h).toMatch(/^sha256:[0-9a-f]{64}$/);
  });

  test("rejects duplicate paths in input", () => {
    expect(() => computeTreeHash([file("x.txt", "A"), file("x.txt", "B")])).toThrow(/duplicate/i);
  });
});
