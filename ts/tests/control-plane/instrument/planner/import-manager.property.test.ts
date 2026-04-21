/**
 * A2-I Layer 5 — P-import-dedup (spec §4.4 I4, §11.2).
 *
 * Invariants tested:
 *   1. Output contains no duplicate (module, name, alias, kind) tuples.
 *   2. Within the output, groups are sorted alphabetically by module.
 *
 * Generator: random multi-edit inputs with overlapping import sets and
 * duplicates. Asserts on `additionalSpecsEmitted` (the dedup output surface).
 */
import { describe, test } from "vitest";
import fc from "fast-check";
import { planImports } from "../../../../src/control-plane/instrument/planner/import-manager.js";
import { fromBytes } from "../../../../src/control-plane/instrument/scanner/source-file.js";
import type {
  ImportSpec,
  InstrumentLanguage,
} from "../../../../src/control-plane/instrument/contract/index.js";

const moduleArb = fc.constantFrom("alpha", "beta", "gamma", "delta", "epsilon");
const nameArb = fc.constantFrom("A", "B", "C", "X", "Y", "Z");
const kindArb = fc.constantFrom<"named" | "default" | "namespace">("named", "default", "namespace");
const aliasArb = fc.option(fc.constantFrom("aAlias", "bAlias"), { nil: undefined });

const specArb: fc.Arbitrary<ImportSpec> = fc
  .record({ module: moduleArb, name: nameArb, alias: aliasArb, kind: kindArb })
  .map((r) => (r.alias === undefined
    ? { module: r.module, name: r.name, kind: r.kind }
    : { module: r.module, name: r.name, alias: r.alias, kind: r.kind }));

const languageArb = fc.constantFrom<InstrumentLanguage>("python", "typescript", "javascript");

function specKey(s: ImportSpec): string {
  return `${s.module}\u0000${s.name}\u0000${s.alias ?? ""}\u0000${s.kind}`;
}

describe("P-import-dedup — I4", () => {
  test("no duplicate tuples; alphabetical by module (100 runs)", () => {
    fc.assert(
      fc.property(
        fc.record({
          language: languageArb,
          specs: fc.array(specArb, { minLength: 0, maxLength: 30 }),
        }),
        ({ language, specs }) => {
          const content = language === "python" ? "x = 1\n" : "const x = 1;\n";
          const sf = fromBytes({
            path: `x.${language === "python" ? "py" : "ts"}`,
            language,
            bytes: Buffer.from(content, "utf-8"),
          });
          const plan = planImports({ sourceFile: sf, importsNeeded: specs });

          // 1. No duplicates.
          const keys = new Set<string>();
          for (const s of plan.additionalSpecsEmitted) {
            const k = specKey(s);
            if (keys.has(k)) throw new Error(`duplicate spec tuple: ${k}`);
            keys.add(k);
          }

          // 2. Alphabetical by module within emitted.
          const modules = plan.additionalSpecsEmitted.map((s) => s.module);
          const sortedModules = modules.slice().sort();
          for (let i = 0; i < modules.length; i += 1) {
            if (modules[i] !== sortedModules[i]) {
              throw new Error(
                `additionalSpecsEmitted not alphabetical by module: got ${JSON.stringify(modules)} expected ${JSON.stringify(sortedModules)}`,
              );
            }
          }
        },
      ),
      { numRuns: 100 },
    );
  });
});
