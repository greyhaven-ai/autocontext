import { describe, test, expect } from "vitest";
import { plugin } from "../../../../../src/control-plane/instrument/detectors/openai-ts/plugin.js";
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

describe("openai-ts detector Gate 3 — factory function", () => {
  test("return new OpenAI() in a function → factoryFunction advisory, no edit", () => {
    const src = "function makeClient() {\n  return new OpenAI();\n}\n";
    const newStart = src.indexOf("new OpenAI");
    const ctorStart = newStart + 4; // after "new "
    const ctorEnd = ctorStart + 6; // "OpenAI"
    const callEnd = newStart + 12; // "new OpenAI()"

    const sf = fakeSourceFile(src, [
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
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
    const src = "const client = new OpenAI();\n";
    const newStart = src.indexOf("new OpenAI");
    const ctorStart = newStart + 4;
    const ctorEnd = ctorStart + 6;
    const callEnd = newStart + 12;

    const sf = fakeSourceFile(src, [
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
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

  test("arrow function return new OpenAI() → factoryFunction advisory", () => {
    const src = "const make = () => {\n  return new OpenAI();\n};\n";
    const newStart = src.indexOf("new OpenAI");
    const ctorStart = newStart + 4;
    const ctorEnd = ctorStart + 6;
    const callEnd = newStart + 12;

    const sf = fakeSourceFile(src, [
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
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
