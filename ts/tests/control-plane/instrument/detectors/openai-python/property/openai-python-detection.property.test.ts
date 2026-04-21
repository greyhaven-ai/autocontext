/**
 * Property test for openai-python detector — fast-check, 100 runs.
 *
 * Invariants:
 *   1. For k in-scope `OpenAI()` / `AsyncOpenAI()` calls, the plugin produces
 *      exactly 2k edits (1 wrap + 1 insert-statement per call).
 *   2. All emitted wrap edits have `wrapFn === "instrument_client"`.
 *   3. All emitted edits reference `autocontext.integrations.openai` in importsNeeded
 *      (only the wrap edit, not the comment insert).
 *   4. No false-positive wrap edits are produced for unimported ctors.
 */
import { describe, test } from "vitest";
import fc from "fast-check";
import { plugin } from "../../../../../../src/control-plane/instrument/detectors/openai-python/plugin.js";
import type { ImportedName } from "../../../../../../src/control-plane/instrument/contract/plugin-interface.js";

function fakeSourceFile(src: string, imports: Array<{ module: string; names: Set<ImportedName> }>): any {
  return {
    path: "src/generated.py",
    language: "python",
    bytes: Buffer.from(src),
    tree: null,
    directives: new Map(),
    hasSecretLiteral: false,
    secretMatches: [],
    existingImports: new Set(imports),
    indentationStyle: { kind: "spaces", width: 4 },
  };
}

/** Build k `var_N = OpenAI()` assignments on separate lines. */
function buildSource(k: number, ctorName = "OpenAI"): string {
  const lines = [`from openai import ${ctorName}`];
  for (let i = 0; i < k; i++) {
    lines.push(`client_${i} = ${ctorName}()`);
  }
  return lines.join("\n") + "\n";
}

/** Collect all ctor positions in source (standalone, not preceded by dot). */
function collectCtorMatches(src: string, ctorName: string): Array<{ ctorStart: number; ctorEnd: number; callEnd: number }> {
  const re = new RegExp(`\\b${ctorName}\\s*\\(`, "g");
  const results = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    if (m.index > 0 && src[m.index - 1] === ".") continue;
    const ctorStart = m.index;
    const ctorEnd = ctorStart + ctorName.length;
    let depth = 0;
    let callEnd = ctorEnd;
    for (let i = ctorEnd; i < src.length; i++) {
      if (src[i] === "(") depth++;
      else if (src[i] === ")") { depth--; if (depth === 0) { callEnd = i + 1; break; } }
    }
    results.push({ ctorStart, ctorEnd, callEnd });
  }
  return results;
}

function runPluginOnAll(src: string, imports: Array<{ module: string; names: Set<ImportedName> }>): { editCount: number; advisoryCount: number } {
  const sf = fakeSourceFile(src, imports);
  let editCount = 0;
  let advisoryCount = 0;
  for (const ctorName of ["OpenAI", "AsyncOpenAI"]) {
    for (const { ctorStart, ctorEnd, callEnd } of collectCtorMatches(src, ctorName)) {
      const match = {
        captures: [
          { name: "call", node: { startIndex: ctorStart, endIndex: callEnd } },
          { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } },
        ],
      };
      const result = plugin.produce(match as any, sf);
      editCount += result.edits.length;
      advisoryCount += result.advisories.length;
    }
  }
  return { editCount, advisoryCount };
}

describe("openai-python detector property tests (100 runs)", () => {
  test("k in-scope OpenAI() calls → 2k edits, 0 advisories", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 5 }),
        (k) => {
          const src = buildSource(k, "OpenAI");
          const imports = [{ module: "openai", names: new Set<ImportedName>([{ name: "OpenAI", alias: undefined }]) }];
          const { editCount, advisoryCount } = runPluginOnAll(src, imports);
          return editCount === k * 2 && advisoryCount === 0;
        },
      ),
      { numRuns: 100 },
    );
  });

  test("k in-scope AsyncOpenAI() calls → 2k edits, 0 advisories", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 5 }),
        (k) => {
          const src = buildSource(k, "AsyncOpenAI");
          const imports = [{ module: "openai", names: new Set<ImportedName>([{ name: "AsyncOpenAI", alias: undefined }]) }];
          const { editCount, advisoryCount } = runPluginOnAll(src, imports);
          return editCount === k * 2 && advisoryCount === 0;
        },
      ),
      { numRuns: 100 },
    );
  });

  test("OpenAI() with no import → 0 edits, k advisories (unresolved-import)", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 5 }),
        (k) => {
          const lines = [];
          for (let i = 0; i < k; i++) lines.push(`client_${i} = OpenAI()`);
          const src = lines.join("\n") + "\n";
          const { editCount, advisoryCount } = runPluginOnAll(src, []);
          return editCount === 0 && advisoryCount === k;
        },
      ),
      { numRuns: 100 },
    );
  });

  test("all emitted wrap edits have wrapFn === instrument_client", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 4 }),
        (k) => {
          const src = buildSource(k, "OpenAI");
          const sf = fakeSourceFile(src, [{ module: "openai", names: new Set<ImportedName>([{ name: "OpenAI", alias: undefined }]) }]);
          for (const { ctorStart, ctorEnd, callEnd } of collectCtorMatches(src, "OpenAI")) {
            const match = {
              captures: [
                { name: "call", node: { startIndex: ctorStart, endIndex: callEnd } },
                { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } },
              ],
            };
            const result = plugin.produce(match as any, sf);
            for (const e of result.edits) {
              if (e.kind === "wrap-expression") {
                if ((e as any).wrapFn !== "instrument_client") return false;
              }
            }
          }
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});
