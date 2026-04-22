/**
 * Property test for openai-ts detector — fast-check, 100 runs.
 *
 * Invariants:
 *   1. For k in-scope `new OpenAI()` calls, the plugin produces
 *      exactly 2k edits (1 wrap + 1 insert-statement per call).
 *   2. All emitted wrap edits have `wrapFn === "instrumentClient"`.
 *   3. All emitted wrap edits reference `autoctx/integrations/openai` in importsNeeded.
 *   4. No false-positive wrap edits are produced for unimported ctors.
 */
import { describe, test } from "vitest";
import fc from "fast-check";
import { plugin } from "../../../../../../src/control-plane/instrument/detectors/openai-ts/plugin.js";
import type { ImportedName } from "../../../../../../src/control-plane/instrument/contract/plugin-interface.js";

function fakeSourceFile(src: string, imports: Array<{ module: string; names: Set<ImportedName> }>): any {
  return {
    path: "src/generated.ts",
    language: "typescript",
    bytes: Buffer.from(src),
    tree: null,
    directives: new Map(),
    hasSecretLiteral: false,
    secretMatches: [],
    existingImports: new Set(imports),
    indentationStyle: { kind: "spaces", width: 2 },
  };
}

/** Build k `const var_N = new OpenAI();` declarations on separate lines. */
function buildSource(k: number, ctorName = "OpenAI"): string {
  const lines = [`import { ${ctorName} } from "openai";`];
  for (let i = 0; i < k; i++) {
    lines.push(`const client_${i} = new ${ctorName}();`);
  }
  return lines.join("\n") + "\n";
}

/** Collect all `new CtorName(...)` positions in source. */
function collectCtorMatches(src: string, ctorName: string): Array<{ newStart: number; ctorStart: number; ctorEnd: number; callEnd: number }> {
  const re = new RegExp(`\\bnew\\s+${ctorName}\\s*\\(`, "g");
  const results = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    const newStart = m.index;
    const ctorStart = newStart + 4; // "new " is 4 bytes
    const ctorEnd = ctorStart + ctorName.length;
    let depth = 0;
    let callEnd = ctorEnd;
    for (let i = ctorEnd; i < src.length; i++) {
      if (src[i] === "(") depth++;
      else if (src[i] === ")") { depth--; if (depth === 0) { callEnd = i + 1; break; } }
    }
    results.push({ newStart, ctorStart, ctorEnd, callEnd });
  }
  return results;
}

function runPluginOnAll(src: string, imports: Array<{ module: string; names: Set<ImportedName> }>): { editCount: number; advisoryCount: number } {
  const sf = fakeSourceFile(src, imports);
  let editCount = 0;
  let advisoryCount = 0;
  for (const ctorName of ["OpenAI"]) {
    for (const { newStart, ctorStart, ctorEnd, callEnd } of collectCtorMatches(src, ctorName)) {
      const match = {
        captures: [
          { name: "call", node: { startIndex: newStart, endIndex: callEnd } },
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

describe("openai-ts detector property tests (100 runs)", () => {
  test("k in-scope new OpenAI() calls → 2k edits, 0 advisories", () => {
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

  test("new OpenAI() with no import → 0 edits, k advisories (unresolved-import)", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 5 }),
        (k) => {
          const lines = [];
          for (let i = 0; i < k; i++) lines.push(`const client_${i} = new OpenAI();`);
          const src = lines.join("\n") + "\n";
          const { editCount, advisoryCount } = runPluginOnAll(src, []);
          return editCount === 0 && advisoryCount === k;
        },
      ),
      { numRuns: 100 },
    );
  });

  test("all emitted wrap edits have wrapFn === instrumentClient", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 4 }),
        (k) => {
          const src = buildSource(k, "OpenAI");
          const sf = fakeSourceFile(src, [{ module: "openai", names: new Set<ImportedName>([{ name: "OpenAI", alias: undefined }]) }]);
          for (const { newStart, ctorStart, ctorEnd, callEnd } of collectCtorMatches(src, "OpenAI")) {
            const match = {
              captures: [
                { name: "call", node: { startIndex: newStart, endIndex: callEnd } },
                { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } },
              ],
            };
            const result = plugin.produce(match as any, sf);
            for (const e of result.edits) {
              if (e.kind === "wrap-expression") {
                if ((e as any).wrapFn !== "instrumentClient") return false;
              }
            }
          }
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });

  test("all emitted wrap edits import from autoctx/integrations/openai", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 4 }),
        (k) => {
          const src = buildSource(k, "OpenAI");
          const sf = fakeSourceFile(src, [{ module: "openai", names: new Set<ImportedName>([{ name: "OpenAI", alias: undefined }]) }]);
          for (const { newStart, ctorStart, ctorEnd, callEnd } of collectCtorMatches(src, "OpenAI")) {
            const match = {
              captures: [
                { name: "call", node: { startIndex: newStart, endIndex: callEnd } },
                { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } },
              ],
            };
            const result = plugin.produce(match as any, sf);
            for (const e of result.edits) {
              if (e.kind === "wrap-expression") {
                const hasAutoctxImport = e.importsNeeded.some(
                  (imp) => imp.module === "autoctx/integrations/openai" && imp.name === "instrumentClient",
                );
                if (!hasAutoctxImport) return false;
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
