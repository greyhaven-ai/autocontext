import { describe, test, expect } from "vitest";
import { plugin } from "../../../../../src/control-plane/instrument/detectors/anthropic-ts/plugin.js";
import type { ImportedName } from "../../../../../src/control-plane/instrument/contract/plugin-interface.js";

function fakeSourceFile(
  bytes: string,
  imports: Array<{ module: string; names: Set<ImportedName> }> = [],
  path = "src/app.ts",
): any {
  return {
    path,
    language: "typescript",
    bytes: Buffer.from(bytes),
    tree: null,
    directives: new Map(),
    hasSecretLiteral: false,
    secretMatches: [],
    existingImports: new Set(imports),
    indentationStyle: { kind: "spaces", width: 2 },
  };
}

describe("anthropic-ts detector Gate 3 — factory function", () => {
  test("return new Anthropic() in a function → factoryFunction advisory, no edit", () => {
    const src = "function makeClient() {\n  return new Anthropic();\n}\n";
    const newStart = src.indexOf("new Anthropic");
    const ctorStart = newStart + 4;
    const ctorEnd = ctorStart + 9; // "Anthropic"
    const callEnd = newStart + 15; // "new Anthropic()"

    const sf = fakeSourceFile(src, [
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: newStart, endIndex: callEnd } },
        { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("factoryFunction");
  });

  test("bare assignment (not return) → no factory advisory, produces edits", () => {
    const src = "const client = new Anthropic();\n";
    const newStart = src.indexOf("new Anthropic");
    const ctorStart = newStart + 4;
    const ctorEnd = ctorStart + 9;
    const callEnd = newStart + 15;

    const sf = fakeSourceFile(src, [
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: newStart, endIndex: callEnd } },
        { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits.length).toBeGreaterThan(0);
    expect(result.advisories.filter((a) => a.kind === "factoryFunction")).toHaveLength(0);
  });

  test("arrow function return new Anthropic() → factoryFunction advisory", () => {
    const src = "const make = () => {\n  return new Anthropic();\n};\n";
    const newStart = src.indexOf("new Anthropic");
    const ctorStart = newStart + 4;
    const ctorEnd = ctorStart + 9;
    const callEnd = newStart + 15;

    const sf = fakeSourceFile(src, [
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: newStart, endIndex: callEnd } },
        { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.advisories.some((a) => a.kind === "factoryFunction")).toBe(true);
    expect(result.edits).toHaveLength(0);
  });
});
