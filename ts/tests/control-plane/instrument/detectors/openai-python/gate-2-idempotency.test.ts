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

describe("openai-python detector Gate 2 — idempotency", () => {
  test("already-wrapped `instrument_client(OpenAI())` → already-wrapped advisory, no edit", () => {
    const src = "instrument_client(OpenAI())";
    // The OpenAI() starts at byte 18
    const sf = fakeSourceFile(src, [
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 18, endIndex: 25 } }, // OpenAI()
        { name: "ctor", node: { startIndex: 18, endIndex: 24 } }, // OpenAI
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("already-wrapped");
  });

  test("not-yet-wrapped `OpenAI()` → produces edits, no already-wrapped advisory", () => {
    const src = "OpenAI()";
    const sf = fakeSourceFile(src, [
      { module: "openai", names: new Set([{ name: "OpenAI", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 8 } },
        { name: "ctor", node: { startIndex: 0, endIndex: 6 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits.length).toBeGreaterThan(0);
    expect(result.advisories.filter((a) => a.kind === "already-wrapped")).toHaveLength(0);
  });
});
