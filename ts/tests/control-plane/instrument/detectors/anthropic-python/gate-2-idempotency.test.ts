import { describe, test, expect } from "vitest";
import { plugin } from "../../../../../src/control-plane/instrument/detectors/anthropic-python/plugin.js";
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

describe("anthropic-python detector Gate 2 — idempotency", () => {
  test("already-wrapped `instrument_client(Anthropic())` → already-wrapped advisory, no edit", () => {
    const src = "instrument_client(Anthropic())";
    // Anthropic() starts at byte 18
    const sf = fakeSourceFile(src, [
      { module: "anthropic", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 18, endIndex: 29 } }, // Anthropic()
        { name: "ctor", node: { startIndex: 18, endIndex: 27 } }, // Anthropic
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits).toHaveLength(0);
    expect(result.advisories).toHaveLength(1);
    expect(result.advisories[0].kind).toBe("already-wrapped");
  });

  test("not-yet-wrapped `Anthropic()` → produces edits, no already-wrapped advisory", () => {
    const src = "Anthropic()";
    const sf = fakeSourceFile(src, [
      { module: "anthropic", names: new Set([{ name: "Anthropic", alias: undefined }]) },
    ]);
    const match = {
      captures: [
        { name: "call", node: { startIndex: 0, endIndex: 11 } },
        { name: "ctor", node: { startIndex: 0, endIndex: 9 } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    expect(result.edits.length).toBeGreaterThan(0);
    expect(result.advisories.filter((a) => a.kind === "already-wrapped")).toHaveLength(0);
  });
});
