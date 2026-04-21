import { describe, test, expect } from "vitest";
import { plugin } from "../../../../../src/control-plane/instrument/detectors/openai-ts/plugin.js";
import type { ImportedName } from "../../../../../src/control-plane/instrument/contract/plugin-interface.js";

function fakeMatch(text: string, start: number, end: number) {
  return {
    captures: [
      { name: "call", node: { startIndex: start, endIndex: end, text } as any },
      { name: "ctor", node: { startIndex: start, endIndex: start + 6, text: "OpenAI" } as any },
    ],
  };
}

function fakeSourceFile(
  imports: Array<{ module: string; names: Set<ImportedName> }>,
  path = "src/app.ts",
  bytes = "new OpenAI()",
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

describe("openai-ts detector Gate 1 — canonical", () => {
  test("canonical new OpenAI() produces one wrap-expression edit", () => {
    const sf = fakeSourceFile([
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
    ], "src/app.ts", "new OpenAI()");
    // new OpenAI() — "new " is 4 bytes, so OpenAI starts at 4
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 12 } },
        { name: "ctor", node: { startIndex: 4, endIndex: 10 } }, // "OpenAI"
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits.length).toBe(2); // wrap + insert-statement comment
    expect(result.edits[0].kind).toBe("wrap-expression");
    expect((result.edits[0] as any).wrapFn).toBe("instrumentClient");
  });

  test("ctor not imported → unresolved-import advisory, no edit", () => {
    const sf = fakeSourceFile([]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 12 } },
        { name: "ctor", node: { startIndex: 4, endIndex: 10 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("unresolved-import");
  });

  test("AzureOpenAI → deferred-sdk-variant advisory, no edit", () => {
    const src = "new AzureOpenAI()";
    const sf = fakeSourceFile([
      { module: "openai", names: new Set([{ name: "AzureOpenAI", alias: undefined }]) },
    ], "src/app.ts", src);
    // "new " = 4 bytes, "AzureOpenAI" = 11 bytes
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 15 } }, // "AzureOpenAI"
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("deferred-sdk-variant");
  });

  test("module-prefixed new openai.OpenAI() produces wrap edit", () => {
    const src = "new openai.OpenAI()";
    const sf = fakeSourceFile([
      { module: "openai", names: new Set([{ name: "openai", alias: "openai" }]) },
    ], "src/app.ts", src);
    // "new " = 4 bytes, "openai" = 6 bytes
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "mod", node: { startIndex: 4, endIndex: 10 } }, // "openai"
        { name: "ctor", node: { startIndex: 11, endIndex: 17 } }, // "OpenAI"
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(2);
    expect(result.edits[0].kind).toBe("wrap-expression");
  });

  test("module-prefixed with unresolved module → unresolved-import advisory", () => {
    const src = "new openai.OpenAI()";
    const sf = fakeSourceFile([]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "mod", node: { startIndex: 4, endIndex: 10 } },
        { name: "ctor", node: { startIndex: 11, endIndex: 17 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("unresolved-import");
  });

  test("aliased canonical `import { OpenAI as Foo } from 'openai'; new Foo()` resolves", () => {
    const src = "new Foo()";
    const sf = fakeSourceFile([
      { module: "openai", names: new Set([{ name: "OpenAI", alias: "Foo" }]) },
    ], "src/a.ts", src);
    // "new " = 4 bytes, "Foo" = 3 bytes
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 7 } }, // "Foo"
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(2);
    expect(result.edits[0].kind).toBe("wrap-expression");
  });

  test("namespace-aliased `import * as oa from 'openai'; new oa.OpenAI()` resolves", () => {
    const src = "new oa.OpenAI()";
    const sf = fakeSourceFile([
      { module: "openai", names: new Set([{ name: "openai", alias: "oa" }]) },
    ], "src/a.ts", src);
    // "new " = 4 bytes, "oa" = 2 bytes
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "mod", node: { startIndex: 4, endIndex: 6 } }, // "oa"
        { name: "ctor", node: { startIndex: 7, endIndex: 13 } }, // "OpenAI"
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(2);
  });

  test("wrap-expression edit imports instrumentClient from autoctx/integrations/openai", () => {
    const src = "new OpenAI()";
    const sf = fakeSourceFile([
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
    ], "src/app.ts", src);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 10 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits[0].importsNeeded).toEqual([
      { module: "autoctx/integrations/openai", name: "instrumentClient", kind: "named" },
    ]);
  });

  test("insert-statement edit has correct comment source", () => {
    const src = "new OpenAI()";
    const sf = fakeSourceFile([
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
    ], "src/app.ts", src);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 10 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits[1].kind).toBe("insert-statement");
    expect((result.edits[1] as any).statementSource).toContain("// autocontext:");
  });
});
