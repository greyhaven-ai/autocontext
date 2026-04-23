import { describe, test, expect } from "vitest";
import { plugin } from "../../../../../src/control-plane/instrument/detectors/anthropic-ts/plugin.js";
import type { ImportedName } from "../../../../../src/control-plane/instrument/contract/plugin-interface.js";

function fakeSourceFile(
  imports: Array<{ module: string; names: Set<ImportedName> }>,
  path = "src/app.ts",
  bytes = "new Anthropic()",
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

describe("anthropic-ts detector Gate 1 — canonical", () => {
  test("canonical new Anthropic() produces one wrap-expression edit", () => {
    const src = "new Anthropic()";
    const sf = fakeSourceFile([
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ], "src/app.ts", src);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 13 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits.length).toBe(2);
    expect(result.edits[0].kind).toBe("wrap-expression");
    expect((result.edits[0] as any).wrapFn).toBe("instrumentClient");
  });

  test("ctor not imported → unresolved-import advisory, no edit", () => {
    const src = "new Anthropic()";
    const sf = fakeSourceFile([], "src/app.ts", src);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 13 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("unresolved-import");
  });

  test("AnthropicBedrock → deferred-sdk-variant advisory, no edit", () => {
    const src = "new AnthropicBedrock()";
    const sf = fakeSourceFile([
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "AnthropicBedrock", alias: undefined }]) },
    ], "src/app.ts", src);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 20 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("deferred-sdk-variant");
  });

  test("AnthropicVertex → deferred-sdk-variant advisory, no edit", () => {
    const src = "new AnthropicVertex()";
    const sf = fakeSourceFile([
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "AnthropicVertex", alias: undefined }]) },
    ], "src/app.ts", src);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 19 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("deferred-sdk-variant");
  });

  test("module-prefixed new anthropic.Anthropic() produces wrap edit", () => {
    const src = "new anthropic.Anthropic()";
    const sf = fakeSourceFile([
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "anthropic", alias: "anthropic" }]) },
    ], "src/app.ts", src);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "mod", node: { startIndex: 4, endIndex: 13 } },
        { name: "ctor", node: { startIndex: 14, endIndex: 23 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(2);
    expect(result.edits[0].kind).toBe("wrap-expression");
  });

  test("module-prefixed with unresolved module → unresolved-import advisory", () => {
    const src = "new anthropic.Anthropic()";
    const sf = fakeSourceFile([]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "mod", node: { startIndex: 4, endIndex: 13 } },
        { name: "ctor", node: { startIndex: 14, endIndex: 23 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("unresolved-import");
  });

  test("aliased canonical new Foo() where Foo = Anthropic resolves", () => {
    const src = "new Foo()";
    const sf = fakeSourceFile([
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "Anthropic", alias: "Foo" }]) },
    ], "src/a.ts", src);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 7 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(2);
    expect(result.edits[0].kind).toBe("wrap-expression");
  });

  test("namespace-aliased new ac.Anthropic() resolves", () => {
    const src = "new ac.Anthropic()";
    const sf = fakeSourceFile([
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "anthropic", alias: "ac" }]) },
    ], "src/a.ts", src);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "mod", node: { startIndex: 4, endIndex: 6 } },
        { name: "ctor", node: { startIndex: 7, endIndex: 16 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(2);
  });

  test("wrap-expression edit imports instrumentClient from autoctx/integrations/anthropic", () => {
    const src = "new Anthropic()";
    const sf = fakeSourceFile([
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ], "src/app.ts", src);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 13 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits[0].importsNeeded).toEqual([
      { module: "autoctx/integrations/anthropic", name: "instrumentClient", kind: "named" },
    ]);
  });
});
