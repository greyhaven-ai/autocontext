/**
 * A2-I Layer 5 — edit-composer property tests (spec §4.4 + §11.2).
 *
 * Covers:
 *   - P-directive-coverage (I2): composeEdits never produces a patch that
 *     modifies bytes in an `off` region.
 *   - P-secret-safety (I3): hasSecretLiteral files always refuse.
 *   - P-right-to-left-application: applying random non-conflicting edits
 *     right-to-left yields the same content as a reference implementation
 *     that sorts and applies in descending-offset order.
 */
import { describe, test } from "vitest";
import fc from "fast-check";
import { composeEdits } from "../../../../src/control-plane/instrument/planner/edit-composer.js";
import { fromBytes } from "../../../../src/control-plane/instrument/scanner/source-file.js";
import type {
  EditDescriptor,
  ReplaceExpressionEdit,
  SourceFile,
  SourceRange,
  WrapExpressionEdit,
} from "../../../../src/control-plane/instrument/contract/index.js";

function rangeFromText(text: string, startByte: number, endByte: number): SourceRange {
  const before = text.slice(0, startByte);
  const sLine = (before.match(/\n/g)?.length ?? 0) + 1;
  const sLastNl = before.lastIndexOf("\n");
  const sCol = startByte - (sLastNl + 1);
  const between = text.slice(0, endByte);
  const eLine = (between.match(/\n/g)?.length ?? 0) + 1;
  const eLastNl = between.lastIndexOf("\n");
  const eCol = endByte - (eLastNl + 1);
  return {
    startByte,
    endByte,
    startLineCol: { line: sLine, col: sCol },
    endLineCol: { line: eLine, col: eCol },
  };
}

function pyFile(content: string, path = "src/main.py"): SourceFile {
  return fromBytes({ path, language: "python", bytes: Buffer.from(content, "utf-8") });
}

// ---------------------------------------------------------------------------
// P-secret-safety (I3) — files with hasSecretLiteral always refuse.
// ---------------------------------------------------------------------------

describe("P-secret-safety — I3", () => {
  test("composeEdits refuses for any file with an injected secret (100 runs)", () => {
    fc.assert(
      fc.property(
        fc.record({
          prefix: fc.string({ maxLength: 20 }).map((s) => s.replace(/[\n\r]/g, "")),
          suffix: fc.string({ maxLength: 20 }).map((s) => s.replace(/[\n\r]/g, "")),
          numEdits: fc.integer({ min: 0, max: 5 }),
        }),
        ({ prefix, suffix, numEdits }) => {
          const secret = "AKIAIOSFODNN7EXAMPLE";
          const content = `${prefix}\n${secret}\n${suffix}\n`;
          const sf = pyFile(content);
          // Build random non-overlapping wrap edits (doesn't matter since safety
          // filter runs first).
          const edits: EditDescriptor[] = [];
          for (let i = 0; i < numEdits; i += 1) {
            const start = Math.min(i * 2, content.length - 1);
            const end = Math.min(start + 1, content.length);
            if (start < end) {
              edits.push({
                kind: "wrap-expression",
                pluginId: "p",
                sourceFilePath: "src/main.py",
                importsNeeded: [],
                range: rangeFromText(content, start, end),
                wrapFn: "w",
              });
            }
          }
          const result = composeEdits({ sourceFile: sf, edits });
          if (result.kind !== "refused" || result.reason.kind !== "secret-literal") {
            throw new Error(
              `expected refused with secret-literal reason; got ${result.kind}${
                result.kind === "refused" ? ` (${result.reason.kind})` : ""
              }`,
            );
          }
        },
      ),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// P-directive-coverage (I2) — no patch ever modifies bytes in an off region.
// ---------------------------------------------------------------------------

describe("P-directive-coverage — I2", () => {
  test("emitted patch never modifies bytes inside an 'off' region (100 runs)", () => {
    fc.assert(
      fc.property(
        fc.record({
          prefixBody: fc.constantFrom("foo()", "bar()", "baz()"),
          offBody: fc.constantFrom("off_a()", "off_b()"),
          postBody: fc.constantFrom("post_x()", "post_y()"),
        }),
        ({ prefixBody, offBody, postBody }) => {
          const content = [
            prefixBody,              // line 1
            "# autocontext: off",    // line 2
            offBody,                 // line 3 — off
            "# autocontext: on",     // line 4
            postBody,                // line 5
            "",
          ].join("\n");
          const sf = pyFile(content);
          const offStart = content.indexOf(offBody);
          const offEnd = offStart + offBody.length;

          // Edit hitting the off region.
          const offEdit: WrapExpressionEdit = {
            kind: "wrap-expression",
            pluginId: "p",
            sourceFilePath: "src/main.py",
            importsNeeded: [],
            range: rangeFromText(content, offStart, offEnd),
            wrapFn: "should_not_apply",
          };
          // Edit hitting the post region.
          const postStart = content.indexOf(postBody);
          const postEnd = postStart + postBody.length;
          const postEdit: WrapExpressionEdit = {
            kind: "wrap-expression",
            pluginId: "p",
            sourceFilePath: "src/main.py",
            importsNeeded: [],
            range: rangeFromText(content, postStart, postEnd),
            wrapFn: "post_wrap",
          };
          const result = composeEdits({ sourceFile: sf, edits: [offEdit, postEdit] });
          if (result.kind !== "patch") return; // refused/conflict acceptable
          const after = result.patch.afterContent ?? "";
          // The off region's CONTENT must remain unchanged.
          if (!after.includes(offBody)) {
            throw new Error(
              `off-region content modified: expected ${JSON.stringify(offBody)} in afterContent`,
            );
          }
          if (after.includes(`should_not_apply(${offBody})`)) {
            throw new Error("off-region edit applied despite directive");
          }
        },
      ),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// P-right-to-left-application — random non-conflicting edits yield the same
// content as a reference implementation.
// ---------------------------------------------------------------------------

describe("P-right-to-left-application", () => {
  test("applying non-conflicting replace edits right-to-left matches reference (100 runs)", () => {
    fc.assert(
      fc.property(
        fc.record({
          baseLength: fc.integer({ min: 10, max: 40 }),
          numEdits: fc.integer({ min: 1, max: 5 }),
          // Use letter chars to keep byte==char width and avoid surprising
          // secret-pattern matches (e.g. long hex → high-entropy hit).
          replacement: fc.constantFrom("X", "YY", "ZZZ"),
        }),
        ({ baseLength, numEdits, replacement }) => {
          const base = "a".repeat(baseLength);
          const content = `${base}\n`;
          const sf = pyFile(content);

          // Build `numEdits` disjoint replace edits at ascending positions.
          const edits: ReplaceExpressionEdit[] = [];
          const step = Math.max(2, Math.floor(baseLength / (numEdits + 1)));
          for (let i = 0; i < numEdits; i += 1) {
            const start = (i + 1) * step - 1;
            if (start + 1 > baseLength) break;
            edits.push({
              kind: "replace-expression",
              pluginId: `p${i}`,
              sourceFilePath: "src/main.py",
              importsNeeded: [],
              range: rangeFromText(content, start, start + 1),
              replacementSource: replacement,
            });
          }
          if (edits.length === 0) return; // vacuously satisfied

          // Reference: sort descending by startByte, apply to original text.
          const ops = edits
            .map((e) => ({ s: e.range.startByte, ep: e.range.endByte, r: e.replacementSource }))
            .sort((a, b) => b.s - a.s);
          let expected = content;
          for (const op of ops) {
            expected = expected.slice(0, op.s) + op.r + expected.slice(op.ep);
          }

          const result = composeEdits({ sourceFile: sf, edits });
          if (result.kind !== "patch") {
            throw new Error(`expected patch; got ${result.kind}`);
          }
          if (result.patch.afterContent !== expected) {
            throw new Error(
              `right-to-left mismatch:\n expected: ${JSON.stringify(expected)}\n   actual: ${JSON.stringify(result.patch.afterContent)}`,
            );
          }
        },
      ),
      { numRuns: 100 },
    );
  });
});
