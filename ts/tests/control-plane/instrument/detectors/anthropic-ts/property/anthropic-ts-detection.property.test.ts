/**
 * Property test for anthropic-ts detector — fast-check, 100 runs.
 *
 * Invariants:
 *   1. For k in-scope `new Anthropic()` calls, the plugin produces
 *      exactly 2k edits (1 wrap + 1 insert-statement per call).
 *   2. All emitted wrap edits have `wrapFn === "instrumentClient"`.
 *   3. All emitted wrap edits reference `autoctx/integrations/anthropic` in importsNeeded.
 *   4. No false-positive wrap edits for unimported ctors.
 */
import { describe, test } from "vitest";
import fc from "fast-check";
import { plugin } from "../../../../../../src/control-plane/instrument/detectors/anthropic-ts/plugin.js";
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

function buildSource(k: number, ctorName = "Anthropic"): string {
  const lines = [`import { ${ctorName} } from "@anthropic-ai/sdk";`];
  for (let i = 0; i < k; i++) {
    lines.push(`const client_${i} = new ${ctorName}();`);
  }
  return lines.join("\n") + "\n";
}

function collectCtorMatches(src: string, ctorName: string): Array<{ newStart: number; ctorStart: number; ctorEnd: number; callEnd: number }> {
  const re = new RegExp(`\\bnew\\s+${ctorName}\\s*\\(`, "g");
  const results = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    const newStart = m.index;
    const ctorStart = newStart + 4;
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
  for (const ctorName of ["Anthropic", "AsyncAnthropic"]) {
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

describe("anthropic-ts detector property tests (100 runs)", () => {
  test("k in-scope new Anthropic() calls → 2k edits, 0 advisories", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 5 }),
        (k) => {
          const src = buildSource(k, "Anthropic");
          const imports = [{ module: "@anthropic-ai/sdk", names: new Set<ImportedName>([{ name: "Anthropic", alias: undefined }]) }];
          const { editCount, advisoryCount } = runPluginOnAll(src, imports);
          return editCount === k * 2 && advisoryCount === 0;
        },
      ),
      { numRuns: 100 },
    );
  });

  test("k in-scope new AsyncAnthropic() calls → 2k edits, 0 advisories", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 5 }),
        (k) => {
          const src = buildSource(k, "AsyncAnthropic");
          const imports = [{ module: "@anthropic-ai/sdk", names: new Set<ImportedName>([{ name: "AsyncAnthropic", alias: undefined }]) }];
          const { editCount, advisoryCount } = runPluginOnAll(src, imports);
          return editCount === k * 2 && advisoryCount === 0;
        },
      ),
      { numRuns: 100 },
    );
  });

  test("new Anthropic() with no import → 0 edits, k advisories (unresolved-import)", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 5 }),
        (k) => {
          const lines = [];
          for (let i = 0; i < k; i++) lines.push(`const client_${i} = new Anthropic();`);
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
          const src = buildSource(k, "Anthropic");
          const sf = fakeSourceFile(src, [{ module: "@anthropic-ai/sdk", names: new Set<ImportedName>([{ name: "Anthropic", alias: undefined }]) }]);
          for (const { newStart, ctorStart, ctorEnd, callEnd } of collectCtorMatches(src, "Anthropic")) {
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

  test("all emitted wrap edits import from autoctx/integrations/anthropic", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 4 }),
        (k) => {
          const src = buildSource(k, "Anthropic");
          const sf = fakeSourceFile(src, [{ module: "@anthropic-ai/sdk", names: new Set<ImportedName>([{ name: "Anthropic", alias: undefined }]) }]);
          for (const { newStart, ctorStart, ctorEnd, callEnd } of collectCtorMatches(src, "Anthropic")) {
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
                  (imp) => imp.module === "autoctx/integrations/anthropic" && imp.name === "instrumentClient",
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
