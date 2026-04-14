import { describe, expect, it } from "vitest";

import { planSimulateInputs } from "../src/cli/simulate-command-workflow.js";

describe("simulate input planning workflow", () => {
  it("builds sweep from inline spec and variables from overrides", async () => {
    const result = await planSimulateInputs({
      values: {
        sweep: "threshold=0.4:0.9:0.1",
        variables: "threshold=0.7,budget=100",
      },
      parseSweepSpec: (raw: string) => [{ name: raw, values: [0.4, 0.5], scale: "linear" }],
      loadSweepFile: () => {
        throw new Error("should not read file");
      },
      parseVariableOverrides: (raw: string) => ({ parsed: raw }),
      readPresetFile: () => {
        throw new Error("should not read preset file");
      },
      parsePreset: () => {
        throw new Error("should not parse preset");
      },
    });

    expect(result).toEqual({
      sweep: [{ name: "threshold=0.4:0.9:0.1", values: [0.4, 0.5], scale: "linear" }],
      variables: { parsed: "threshold=0.7,budget=100" },
    });
  });

  it("loads sweep from file when no inline sweep is provided", async () => {
    const result = await planSimulateInputs({
      values: {
        "sweep-file": "sweep.json",
      },
      parseSweepSpec: () => {
        throw new Error("should not parse inline sweep");
      },
      loadSweepFile: (path: string) => [{ name: path, values: [1], scale: "linear" }],
      parseVariableOverrides: () => ({}),
      readPresetFile: () => "{}",
      parsePreset: () => null,
    });

    expect(result.sweep).toEqual([{ name: "sweep.json", values: [1], scale: "linear" }]);
    expect(result.variables).toBeUndefined();
  });

  it("merges preset variables under explicit overrides", async () => {
    const result = await planSimulateInputs({
      values: {
        variables: "threshold=0.7,budget=100",
        preset: "aggressive",
        "preset-file": "presets.json",
      },
      parseSweepSpec: () => [],
      loadSweepFile: () => [],
      parseVariableOverrides: () => ({ threshold: 0.7, budget: 100 }),
      readPresetFile: (path: string) => `contents:${path}`,
      parsePreset: (preset: string, raw: string) => {
        expect(preset).toBe("aggressive");
        expect(raw).toBe("contents:presets.json");
        return { threshold: 0.9, retries: 2 };
      },
    });

    expect(result.variables).toEqual({ threshold: 0.7, retries: 2, budget: 100 });
  });

  it("fails before provider resolution when a requested preset is missing", async () => {
    await expect(
      planSimulateInputs({
        values: {
          description: "simulate a deployment",
          preset: "aggressive",
          "preset-file": "presets.json",
        },
        parseSweepSpec: () => [],
        loadSweepFile: () => [],
        parseVariableOverrides: () => ({}),
        readPresetFile: () => "{}",
        parsePreset: () => null,
      }),
    ).rejects.toThrow(
      "Error: preset 'aggressive' was not found or 'presets.json' is not valid preset JSON.",
    );
  });
});
