import { describe, test, expect } from "vitest";
import { plugin } from "../../../../../src/control-plane/instrument/detectors/openai-python/plugin.js";
import type { ImportedName } from "../../../../../src/control-plane/instrument/contract/plugin-interface.js";

function fakeMatch(text: string, start: number, end: number) {
  return {
    captures: [
      { name: "call", node: { startIndex: start, endIndex: end, text } as any },
      { name: "ctor", node: { startIndex: start, endIndex: start + 6, text: "OpenAI" } as any },
    ],
  };
}

function fakeSourceFile(imports: Array<{ module: string; names: Set<ImportedName> }>, path = "src/app.py", bytes = "OpenAI()"): any {
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

describe("openai-python detector Gate 1 — canonical", () => {
  test("canonical OpenAI() produces one wrap-expression edit", () => {
    const sf = fakeSourceFile([
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
    ]);
    const result = plugin.produce(fakeMatch("OpenAI()", 0, 8), sf);
    expect(result.edits.length).toBe(2); // wrap + insert-statement comment
    expect(result.edits[0].kind).toBe("wrap-expression");
    expect((result.edits[0] as any).wrapFn).toBe("instrument_client");
  });

  test("ctor not imported → unresolved-import advisory, no edit", () => {
    const sf = fakeSourceFile([]);
    const result = plugin.produce(fakeMatch("OpenAI()", 0, 8), sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("unresolved-import");
  });

  test("AzureOpenAI → deferred-sdk-variant advisory, no edit", () => {
    const sf = fakeSourceFile([
      { module: "openai", names: new Set([{ name: "AzureOpenAI", alias: undefined }]) },
    ], "src/app.py", "AzureOpenAI()");
    const matchAzure = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 12 } },
        { name: "ctor", node: { startIndex: 0, endIndex: 11 } },
      ],
    };
    const result = plugin.produce(matchAzure as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("deferred-sdk-variant");
  });

  test("module-prefixed openai.OpenAI() produces wrap edit", () => {
    const sf = fakeSourceFile([
      { module: "openai", names: new Set([{ name: "openai", alias: "openai" }]) },
    ], "src/app.py", "openai.OpenAI()");
    const matchMod = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 14 } },
        { name: "mod", node: { startIndex: 0, endIndex: 6 } },
        { name: "ctor", node: { startIndex: 7, endIndex: 13 } },
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
        { name: "call", node: { startIndex: 0, endIndex: 14 } },
        { name: "mod", node: { startIndex: 0, endIndex: 6 } },
        { name: "ctor", node: { startIndex: 7, endIndex: 13 } },
      ],
    };
    const result = plugin.produce(matchMod as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("unresolved-import");
  });
});
