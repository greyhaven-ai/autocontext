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

describe("openai-ts detector Gate 2 — idempotency", () => {
  test("already-wrapped `instrumentClient(new OpenAI())` → already-wrapped advisory, no edit", () => {
    // "instrumentClient(" is 17 bytes, then "new OpenAI()"
    const src = "instrumentClient(new OpenAI())";
    // "new " = 4 bytes, so in "instrumentClient(new OpenAI())"
    //  "new" starts at 17, "OpenAI" starts at 21 (17 + 4)
    const newStart = src.indexOf("new OpenAI");
    const ctorStart = newStart + 4; // after "new "
    const ctorEnd = ctorStart + 6; // "OpenAI"
    const callEnd = src.indexOf(")") + 1; // first ")" closes new OpenAI()

    // We need to find the correct call end: it's the ")" that closes new OpenAI()
    // The inner call: "new OpenAI()" — openParen at ctorEnd, closeParen one char later
    const innerCallEnd = src.lastIndexOf(")") + 1 - 1; // exclude outer
    // Let's just be explicit: "new OpenAI()" spans [17, 29)
    const sf = fakeSourceFile(src, [
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: newStart, endIndex: newStart + 12 } }, // "new OpenAI()"
        { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } }, // "OpenAI"
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("already-wrapped");
  });

  test("not-yet-wrapped `new OpenAI()` → produces edits, no already-wrapped advisory", () => {
    const src = "new OpenAI()";
    const sf = fakeSourceFile(src, [
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: src.length } },
        { name: "ctor", node: { startIndex: 4, endIndex: 10 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits.length).toBeGreaterThan(0);
    expect(result.advisories.filter((a) => a.kind === "already-wrapped")).toHaveLength(0);
  });

  test("instrumentClient with whitespace before paren → still detected as wrapped", () => {
    const src = "instrumentClient( new OpenAI())";
    const newStart = src.indexOf("new OpenAI");
    const ctorStart = newStart + 4;
    const sf = fakeSourceFile(src, [
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: newStart, endIndex: newStart + 12 } },
        { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorStart + 6 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.advisories.some((a) => a.kind === "already-wrapped")).toBe(true);
  });
});
