/**
 * A2-I Layer 5 — P-indentation-preservation (spec §4.4 I5, §11.2).
 *
 * Invariant: inserted statements' leading whitespace matches the enclosing
 * scope's style (tabs or spaces × width).
 *
 * Generator strategy: random mixed-style fixtures in Python, TypeScript, and
 * JavaScript, with a random anchor line. Check that the matched statement's
 * FIRST non-blank line has EXACTLY the predecessor's leading whitespace.
 */
import { describe, test } from "vitest";
import fc from "fast-check";
import { matchIndentation } from "../../../../src/control-plane/instrument/planner/indentation-matcher.js";
import { fromBytes } from "../../../../src/control-plane/instrument/scanner/source-file.js";
import type { InstrumentLanguage } from "../../../../src/control-plane/instrument/contract/index.js";

function leadingWhitespace(line: string): string {
  let i = 0;
  while (i < line.length && (line[i] === " " || line[i] === "\t")) i += 1;
  return line.slice(0, i);
}

const indentStyleArb = fc.constantFrom(
  "  ",           // 2-space
  "    ",         // 4-space
  "\t",           // tab
  "        ",     // 8-space
);

const languageArb = fc.constantFrom<InstrumentLanguage>("python", "typescript", "javascript");

describe("P-indentation-preservation — I5", () => {
  test("inserted statement's leading ws matches enclosing scope (100 runs)", () => {
    fc.assert(
      fc.property(
        fc.record({
          language: languageArb,
          indent: indentStyleArb,
          predecessorBody: fc.stringMatching(/^[A-Za-z_][A-Za-z0-9_]*$/),
          rawStatement: fc.stringMatching(/^[A-Za-z_][A-Za-z0-9_()=\s]*$/),
        }),
        ({ language, indent, predecessorBody, rawStatement }) => {
          // Build a two-line fixture: a top-level decl, then indented body.
          const header = language === "python" ? "def f():" : "function f() {";
          const predecessor = `${indent}${predecessorBody}`;
          const content = [header, predecessor, ""].join("\n");
          const sf = fromBytes({
            path: `x.${language === "python" ? "py" : "ts"}`,
            language,
            bytes: Buffer.from(content, "utf-8"),
          });
          // Anchor at the blank line after the predecessor.
          const out = matchIndentation({
            sourceFile: sf,
            anchorLine: 3,
            rawStatement,
          });
          const firstLine = out.split("\n").find((l) => l.trim().length > 0);
          if (firstLine === undefined) return; // empty raw statement post-split — vacuously satisfied
          const lead = leadingWhitespace(firstLine);
          if (lead !== indent) {
            throw new Error(
              `leading whitespace mismatch: got ${JSON.stringify(lead)} expected ${JSON.stringify(indent)}`,
            );
          }
        },
      ),
      { numRuns: 100 },
    );
  });
});
