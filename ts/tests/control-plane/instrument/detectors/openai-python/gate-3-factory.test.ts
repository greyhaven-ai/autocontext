import { describe, test, expect } from "vitest";
import { plugin } from "../../../../../src/control-plane/instrument/detectors/openai-python/plugin.js";
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

describe("openai-python detector Gate 3 — factory function", () => {
  test("return OpenAI() in a function → factoryFunction advisory, no edit", () => {
    const src = "def make():\n    return OpenAI()\n";
    // OpenAI() is at byte 19 (after "def make():\n    return ")
    const openaiStart = src.indexOf("OpenAI()");
    const sf = fakeSourceFile(src, [
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: openaiStart, endIndex: openaiStart + 8 } },
        { name: "ctor", node: { startIndex: openaiStart, endIndex: openaiStart + 6 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("factoryFunction");
  });

  test("bare assignment (not return) → no factory advisory, produces edits", () => {
    const src = "client = OpenAI()\n";
    const openaiStart = src.indexOf("OpenAI()");
    const sf = fakeSourceFile(src, [
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: openaiStart, endIndex: openaiStart + 8 } },
        { name: "ctor", node: { startIndex: openaiStart, endIndex: openaiStart + 6 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits.length).toBeGreaterThan(0);
    expect(result.advisories.filter((a) => a.kind === "factoryFunction")).toHaveLength(0);
  });
});
