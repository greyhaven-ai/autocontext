import { describe, test, expect } from "vitest";
import { plugin } from "../../../../../src/control-plane/instrument/detectors/anthropic-python/plugin.js";
import type { ImportedName } from "../../../../../src/control-plane/instrument/contract/plugin-interface.js";

function fakeMatch(text: string, start: number, end: number) {
  return {
    captures: [
      { name: "call", node: { startIndex: start, endIndex: end, text } as any },
      { name: "ctor", node: { startIndex: start, endIndex: start + 9, text: "Anthropic" } as any },
    ],
  };
}

function fakeSourceFile(imports: Array<{ module: string; names: Set<ImportedName> }>, path = "src/app.py", bytes = "Anthropic()"): any {
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

describe("anthropic-python detector Gate 1 — canonical", () => {
  test("canonical Anthropic() produces one wrap-expression edit", () => {
    const sf = fakeSourceFile([
      { module: "anthropic", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ]);
    const result = plugin.produce(fakeMatch("Anthropic()", 0, 11), sf);
    expect(result.edits.length).toBe(2); // wrap + insert-statement comment
    expect(result.edits[0].kind).toBe("wrap-expression");
    expect((result.edits[0] as any).wrapFn).toBe("instrument_client");
  });

  test("ctor not imported → unresolved-import advisory, no edit", () => {
    const sf = fakeSourceFile([]);
    const result = plugin.produce(fakeMatch("Anthropic()", 0, 11), sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("unresolved-import");
  });

  test("AnthropicBedrock → deferred-sdk-variant advisory, no edit", () => {
    const sf = fakeSourceFile([
      { module: "anthropic", names: new Set([{ name: "AnthropicBedrock", alias: undefined }]) },
    ], "src/app.py", "AnthropicBedrock()");
    const matchBedrock = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 18 } },
        { name: "ctor", node: { startIndex: 0, endIndex: 16 } },
      ],
    };
    const result = plugin.produce(matchBedrock as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("deferred-sdk-variant");
  });

  test("AnthropicVertex → deferred-sdk-variant advisory, no edit", () => {
    const sf = fakeSourceFile([
      { module: "anthropic", names: new Set([{ name: "AnthropicVertex", alias: undefined }]) },
    ], "src/app.py", "AnthropicVertex()");
    const matchVertex = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 17 } },
        { name: "ctor", node: { startIndex: 0, endIndex: 15 } },
      ],
    };
    const result = plugin.produce(matchVertex as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("deferred-sdk-variant");
  });

  test("module-prefixed anthropic.Anthropic() produces wrap edit", () => {
    const sf = fakeSourceFile([
      { module: "anthropic", names: new Set([{ name: "anthropic", alias: "anthropic" }]) },
    ], "src/app.py", "anthropic.Anthropic()");
    const matchMod = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 20 } },
        { name: "mod", node: { startIndex: 0, endIndex: 9 } },
        { name: "ctor", node: { startIndex: 10, endIndex: 19 } },
      ],
    };
    const result = plugin.produce(matchMod as any, sf);
    expect(result.edits).toHaveLength(2);
    expect(result.edits[0].kind).toBe("wrap-expression");
  });

  test("module-prefixed with unresolved module → unresolved-import advisory", () => {
    const sf = fakeSourceFile([]);
    const matchMod = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 20 } },
        { name: "mod", node: { startIndex: 0, endIndex: 9 } },
        { name: "ctor", node: { startIndex: 10, endIndex: 19 } },
      ],
    };
    const result = plugin.produce(matchMod as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("unresolved-import");
  });

  test("aliased canonical `from anthropic import Anthropic as Foo; Foo()` resolves", () => {
    const sf = fakeSourceFile([
      { module: "anthropic", names: new Set([{ name: "Anthropic", alias: "Foo" }]) },
    ], "src/a.py", "Foo()");
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 5 } },
        { name: "ctor", node: { startIndex: 0, endIndex: 3 } }, // "Foo"
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(2);
  });

  test("aliased namespace `import anthropic as ac; ac.Anthropic()` resolves", () => {
    const sf = fakeSourceFile([
      { module: "anthropic", names: new Set([{ name: "anthropic", alias: "ac" }]) },
    ], "src/a.py", "ac.Anthropic()");
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 14 } },
        { name: "mod", node: { startIndex: 0, endIndex: 2 } }, // "ac"
        { name: "ctor", node: { startIndex: 3, endIndex: 12 } }, // "Anthropic"
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(2);
  });
});
