/**
 * A2-I Layer 5 — P-conflict-safety (spec §4.4 I6, §11.2).
 *
 * Invariant: detectConflicts never returns kind:"ok" with deduplicatedEdits
 * that contain a pair of overlapping byte-ranges.
 */
import { describe, test } from "vitest";
import fc from "fast-check";
import { detectConflicts } from "../../../../src/control-plane/instrument/planner/conflict-detector.js";
import type {
  EditDescriptor,
  SourceRange,
  WrapExpressionEdit,
  ReplaceExpressionEdit,
  InsertStatementEdit,
} from "../../../../src/control-plane/instrument/contract/index.js";

const rangeArb = fc
  .tuple(fc.integer({ min: 0, max: 1000 }), fc.integer({ min: 0, max: 100 }))
  .map(([start, len]): SourceRange => ({
    startByte: start,
    endByte: start + len + 1, // ensure non-empty
    startLineCol: { line: 1, col: start },
    endLineCol: { line: 1, col: start + len + 1 },
  }));

const wrapEditArb: fc.Arbitrary<WrapExpressionEdit> = fc
  .record({
    pluginId: fc.constantFrom("plugin-a", "plugin-b", "plugin-c"),
    range: rangeArb,
    wrapFn: fc.constantFrom("f", "g", "h"),
  })
  .map((r): WrapExpressionEdit => ({
    kind: "wrap-expression",
    pluginId: r.pluginId,
    sourceFilePath: "x.py",
    importsNeeded: [],
    range: r.range,
    wrapFn: r.wrapFn,
  }));

const replaceEditArb: fc.Arbitrary<ReplaceExpressionEdit> = fc
  .record({
    pluginId: fc.constantFrom("plugin-a", "plugin-b"),
    range: rangeArb,
    replacementSource: fc.string(),
  })
  .map((r): ReplaceExpressionEdit => ({
    kind: "replace-expression",
    pluginId: r.pluginId,
    sourceFilePath: "x.py",
    importsNeeded: [],
    range: r.range,
    replacementSource: r.replacementSource,
  }));

const insertEditArb: fc.Arbitrary<InsertStatementEdit> = fc
  .record({
    pluginId: fc.constantFrom("plugin-a", "plugin-b"),
    anchor: rangeArb,
    anchorKind: fc.constantFrom<"before" | "after">("before", "after"),
    statementSource: fc.string(),
  })
  .map((r): InsertStatementEdit => ({
    kind: "insert-statement",
    pluginId: r.pluginId,
    sourceFilePath: "x.py",
    importsNeeded: [],
    anchor: { kind: r.anchorKind, range: r.anchor },
    statementSource: r.statementSource,
  }));

const editArb: fc.Arbitrary<EditDescriptor> = fc.oneof(wrapEditArb, replaceEditArb, insertEditArb);

function getRanges(e: EditDescriptor): SourceRange[] {
  if (e.kind === "wrap-expression" || e.kind === "replace-expression") return [e.range];
  return [e.anchor.range];
}

function overlaps(a: SourceRange, b: SourceRange): boolean {
  return a.startByte < b.endByte && b.startByte < a.endByte;
}

describe("P-conflict-safety — I6", () => {
  test("deduplicatedEdits never contain overlapping range pairs (100 runs)", () => {
    fc.assert(
      fc.property(fc.array(editArb, { minLength: 0, maxLength: 12 }), (edits) => {
        const report = detectConflicts(edits);
        if (report.kind !== "ok") return; // conflict is allowed
        for (let i = 0; i < report.deduplicatedEdits.length; i += 1) {
          for (let j = i + 1; j < report.deduplicatedEdits.length; j += 1) {
            const ea = report.deduplicatedEdits[i]!;
            const eb = report.deduplicatedEdits[j]!;
            for (const ra of getRanges(ea)) {
              for (const rb of getRanges(eb)) {
                // InsertStatementEdit anchor ranges are allowed to coincide with
                // OTHER insert anchors (two insertions at the same anchor), per
                // the unit tests. Only edits that modify content (wrap/replace)
                // must be non-overlapping.
                const bothAreInsert = ea.kind === "insert-statement" && eb.kind === "insert-statement";
                if (bothAreInsert) continue;
                if (overlaps(ra, rb)) {
                  throw new Error(
                    `overlapping ranges in deduplicatedEdits: [${ra.startByte},${ra.endByte}) vs [${rb.startByte},${rb.endByte})`,
                  );
                }
              }
            }
          }
        }
      }),
      { numRuns: 100 },
    );
  });
});
