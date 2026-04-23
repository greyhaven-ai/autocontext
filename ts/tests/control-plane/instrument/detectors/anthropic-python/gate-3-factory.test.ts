import { describe, test, expect } from "vitest";
import { plugin } from "../../../../../src/control-plane/instrument/detectors/anthropic-python/plugin.js";
import type { ImportedName } from "../../../../../src/control-plane/instrument/contract/plugin-interface.js";

function fakeSourceFile(bytes: string, imports: Array<{ module: string; names: Set<ImportedName> }> = [], path = "src/app.py"): any {
  return {
    path,
    language: "python",
    bytes: Buffer.from(bytes),
    tree: null,
    directives: new Map(),
    hasSecretLiteral: false,
    secretMatches: [],
    existingImports: new Set(imports),
    indentationStyle: { kind: "spaces", width: 4 },
  };
}

describe("anthropic-python detector Gate 3 — factory function", () => {
  test("return Anthropic() in a function → factoryFunction advisory, no edit", () => {
    const src = "def make():\n    return Anthropic()\n";
    // Anthropic() is after "def make():\n    return "
    const anthropicStart = src.indexOf("Anthropic()");
    const sf = fakeSourceFile(src, [
      { module: "anthropic", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: anthropicStart, endIndex: anthropicStart + 11 } },
        { name: "ctor", node: { startIndex: anthropicStart, endIndex: anthropicStart + 9 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("factoryFunction");
  });

  test("bare assignment (not return) → no factory advisory, produces edits", () => {
    const src = "client = Anthropic()\n";
    const anthropicStart = src.indexOf("Anthropic()");
    const sf = fakeSourceFile(src, [
      { module: "anthropic", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: anthropicStart, endIndex: anthropicStart + 11 } },
        { name: "ctor", node: { startIndex: anthropicStart, endIndex: anthropicStart + 9 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits.length).toBeGreaterThan(0);
    expect(result.advisories.filter((a) => a.kind === "factoryFunction")).toHaveLength(0);
  });
});
