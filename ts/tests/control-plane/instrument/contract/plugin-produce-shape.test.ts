import { describe, test, expect } from "vitest";
import type { DetectorPlugin, PluginAdvisory } from "../../../../src/control-plane/instrument/contract/plugin-interface.js";

describe("DetectorPlugin.produce() widened return shape", () => {
  test("type allows {edits, advisories} return", () => {
    const p: DetectorPlugin = {
      id: "@test/p",
      supports: { language: "python", sdkName: "openai" },
      treeSitterQueries: [],
      produce: (_match, _sourceFile) => ({ edits: [], advisories: [] }),
    };
    const result = p.produce({ captures: [] }, {} as any);
    expect(result).toHaveProperty("edits");
    expect(result).toHaveProperty("advisories");
  });

  test("PluginAdvisory has required kind values", () => {
    const a: PluginAdvisory = {
      pluginId: "@test/p",
      sourceFilePath: "x.py",
      range: { startByte: 0, endByte: 0, startLineCol: { line: 1, col: 0 }, endLineCol: { line: 1, col: 0 } },
      kind: "factoryFunction",
      reason: "r",
    };
    expect(a.kind).toBe("factoryFunction");
  });
});
