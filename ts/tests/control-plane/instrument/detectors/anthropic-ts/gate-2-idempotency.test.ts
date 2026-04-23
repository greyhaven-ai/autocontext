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

describe("anthropic-ts detector Gate 2 — idempotency", () => {
  test("already-wrapped instrumentClient(new Anthropic()) → already-wrapped advisory, no edit", () => {
    // "instrumentClient(" is 17 bytes, then "new Anthropic()"
    const src = "instrumentClient(new Anthropic())";
    const newStart = src.indexOf("new Anthropic");
    const ctorStart = newStart + 4; // after "new "
    const ctorEnd = ctorStart + 9; // "Anthropic"
    const sf = fakeSourceFile(src, [
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: newStart, endIndex: newStart + 15 } }, // "new Anthropic()"
        { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } }, // "Anthropic"
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("already-wrapped");
  });

  test("not-yet-wrapped new Anthropic() → produces edits, no already-wrapped advisory", () => {
    const src = "new Anthropic()";
    const sf = fakeSourceFile(src, [
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 13 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits.length).toBeGreaterThan(0);
    expect(result.advisories.filter((a) => a.kind === "already-wrapped")).toHaveLength(0);
  });

  test("instrumentClient with whitespace before paren → still detected as wrapped", () => {
    const src = "instrumentClient( new Anthropic())";
    const newStart = src.indexOf("new Anthropic");
    const ctorStart = newStart + 4;
    const sf = fakeSourceFile(src, [
      { module: "@anthropic-ai/sdk", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: newStart, endIndex: newStart + 15 } },
        { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorStart + 9 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.advisories.some((a) => a.kind === "already-wrapped")).toBe(true);
  });
});
