import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";

const SRC_DIR = join(__dirname, "..", "src");

function isConstAssertion(node: ts.AsExpression): boolean {
  return ts.isTypeReferenceNode(node.type)
    && ts.isIdentifier(node.type.typeName)
    && node.type.typeName.text === "const";
}

function countAssertionsInFile(full: string): number {
  const content = readFileSync(full, "utf-8");
  const sourceFile = ts.createSourceFile(
    full,
    content,
    ts.ScriptTarget.Latest,
    true,
    full.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );

  let count = 0;
  function walk(node: ts.Node) {
    if (ts.isAsExpression(node) && !isConstAssertion(node)) {
      count += 1;
    }
    ts.forEachChild(node, walk);
  }

  walk(sourceFile);
  return count;
}

function countAssertions(dir: string): Map<string, number> {
  const counts = new Map<string, number>();

  function walk(d: string) {
    for (const entry of readdirSync(d)) {
      const full = join(d, entry);
      if (statSync(full).isDirectory()) {
        if (entry !== "node_modules") walk(full);
      } else if (full.endsWith(".ts") || full.endsWith(".tsx")) {
        const assertionCount = countAssertionsInFile(full);
        if (assertionCount > 0) {
          counts.set(relative(SRC_DIR, full), assertionCount);
        }
      }
    }
  }

  walk(dir);
  return counts;
}

describe("TypeScript type assertion budget", () => {
  const counts = countAssertions(SRC_DIR);
  const total = Array.from(counts.values()).reduce((a, b) => a + b, 0);

  it("total assertions should be under budget", () => {
    // Budget: enforce no regression from current baseline
    // Bumped to 550 when control-plane/contract/ landed (branded-ID parsers
    // require `as Brand` casts — phantom types have no runtime representation).
    // Bumped to 565 when control-plane/registry/ (Layer 4) landed — fs-based
    // stores must cast strings parsed from on-disk JSON back to branded
    // ArtifactId/Scenario/EnvironmentTag/ContentHash, and the listStatePointers
    // walk reconstructs branded path components from directory entry names.
    // Bumped to 600 when control-plane/cli/ (Layer 8) landed — the tiny
    // in-house flag parser returns `string | string[] | undefined` (to keep
    // the parser itself generic across option specs), so each command handler
    // narrows with `as string` / `as ActuatorType` at point-of-use. A typed
    // parser would move the casts inside that one module but not eliminate
    // them; the spread saves maintenance cost. Also covers a handful of
    // branded-id casts where the CLI builds a filter object from parsed
    // flags, and an OutputMode cast where the formatter accepts the narrowed
    // union.
    // Bumped to 610 when control-plane/actuators/fine-tuned-model/legacy-adapter.ts
    // (Layer 11) landed — migrating JSON-parsed `unknown` documents into
    // branded Scenario/EnvironmentTag and narrowed ActivationState values
    // requires a small cluster of `as Brand` / `as ActivationState` casts
    // after manual type-guards. The alternative (a schema library adapter
    // emitting branded types) was rejected as disproportionate for a v1
    // one-shot migration path.
    expect(total).toBeLessThanOrEqual(610);
  });

  it("mission/store.ts should use row types instead of inline casts", () => {
    const missionStore = counts.get("mission/store.ts") ?? 0;
    // Was 45, reduced to 31 with row interfaces + mapper functions
    expect(missionStore).toBeLessThanOrEqual(35);
  });

  it("storage/index.ts should use row types consistently", () => {
    const storage = counts.get("storage/index.ts") ?? 0;
    // AST-based counting finds 26 current non-const assertions here.
    expect(storage).toBeLessThanOrEqual(26);
  });
});
