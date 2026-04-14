/**
 * AC-454: Richer variable/sweep DSL for simulate.
 *
 * Tests the extended sweep parser supporting categorical values,
 * logarithmic scales, sweep-file loading, and presets.
 */

import { describe, it, expect, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  parseSweepSpec,
  loadSweepFile,
  parsePreset,
  type SweepDimension,
} from "../src/simulation/sweep-dsl.js";

// ---------------------------------------------------------------------------
// Categorical sweeps
// ---------------------------------------------------------------------------

describe("categorical sweeps", () => {
  it("parses key=val1,val2,val3 as categorical", () => {
    const dims = parseSweepSpec("strategy=aggressive,conservative,balanced");
    expect(dims.length).toBe(1);
    expect(dims[0].name).toBe("strategy");
    expect(dims[0].values).toEqual(["aggressive", "conservative", "balanced"]);
    expect(dims[0].scale).toBe("categorical");
  });

  it("distinguishes categorical from numeric range", () => {
    const dims = parseSweepSpec("threshold=0.4:0.9:0.1,mode=fast,slow");
    expect(dims.length).toBe(2);
    expect(dims[0].name).toBe("threshold");
    expect(dims[0].scale).toBe("linear");
    expect(typeof dims[0].values[0]).toBe("number");
    expect(dims[1].name).toBe("mode");
    expect(dims[1].scale).toBe("categorical");
    expect(dims[1].values).toEqual(["fast", "slow"]);
  });

  it("single categorical value is valid", () => {
    const dims = parseSweepSpec("env=production");
    expect(dims.length).toBe(1);
    expect(dims[0].values).toEqual(["production"]);
  });

  it("preserves numeric categorical values as numbers", () => {
    const dims = parseSweepSpec("threshold=0.1,0.5,0.9");
    expect(dims).toHaveLength(1);
    expect(dims[0].values).toEqual([0.1, 0.5, 0.9]);
    expect(typeof dims[0].values[0]).toBe("number");
  });
});

// ---------------------------------------------------------------------------
// Logarithmic scale
// ---------------------------------------------------------------------------

describe("logarithmic sweeps", () => {
  it("parses key=log:min:max:steps format", () => {
    const dims = parseSweepSpec("learning_rate=log:0.001:1.0:4");
    expect(dims.length).toBe(1);
    expect(dims[0].name).toBe("learning_rate");
    expect(dims[0].scale).toBe("log");
    expect(dims[0].values.length).toBe(4);
    // Values should be logarithmically spaced
    expect(dims[0].values[0]).toBeCloseTo(0.001, 3);
    expect(dims[0].values[dims[0].values.length - 1]).toBeCloseTo(1.0, 1);
    // Middle values should not be linearly spaced
    const linearMid = (0.001 + 1.0) / 2;
    expect(Math.abs((dims[0].values[1] as number) - linearMid)).toBeGreaterThan(0.01);
  });

  it("log scale produces strictly increasing values", () => {
    const dims = parseSweepSpec("lr=log:0.0001:1.0:6");
    const vals = dims[0].values as number[];
    for (let i = 1; i < vals.length; i++) {
      expect(vals[i]).toBeGreaterThan(vals[i - 1]);
    }
  });
});

// ---------------------------------------------------------------------------
// Linear range (existing, regression)
// ---------------------------------------------------------------------------

describe("linear sweeps (regression)", () => {
  it("parses min:max:step format", () => {
    const dims = parseSweepSpec("threshold=0.4:0.9:0.1");
    expect(dims.length).toBe(1);
    expect(dims[0].name).toBe("threshold");
    expect(dims[0].scale).toBe("linear");
    expect(dims[0].values.length).toBeGreaterThan(3);
    expect(typeof dims[0].values[0]).toBe("number");
  });

  it("normalizes repeating linear step values to stable four-decimal sweep cells", () => {
    const dims = parseSweepSpec("threshold=0:1:0.3333333333");
    expect(dims).toHaveLength(1);
    expect(dims[0].values).toEqual([0, 0.3333, 0.6667, 1]);
  });

  it("parses multiple dimensions", () => {
    const dims = parseSweepSpec("threshold=0.4:0.9:0.1,budget=50:200:50");
    expect(dims.length).toBe(2);
  });

  it("supports semicolons as explicit dimension separators", () => {
    const dims = parseSweepSpec("threshold=0.4:0.9:0.1;mode=fast,slow");
    expect(dims.length).toBe(2);
    expect(dims[0]).toMatchObject({ name: "threshold", scale: "linear" });
    expect(dims[1]).toMatchObject({ name: "mode", scale: "categorical" });
    expect(dims[1].values).toEqual(["fast", "slow"]);
  });

  it("returns empty for empty string", () => {
    expect(parseSweepSpec("")).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Sweep file loading
// ---------------------------------------------------------------------------

describe("loadSweepFile", () => {
  let tmpDir: string;
  afterEach(() => { if (tmpDir) rmSync(tmpDir, { recursive: true, force: true }); });

  it("loads sweep config from a JSON file", () => {
    tmpDir = mkdtempSync(join(tmpdir(), "sweep-dsl-"));
    const config = {
      dimensions: [
        { name: "threshold", min: 0.3, max: 0.9, step: 0.2 },
        { name: "strategy", values: ["aggressive", "balanced"] },
      ],
    };
    const filePath = join(tmpDir, "sweep.json");
    writeFileSync(filePath, JSON.stringify(config), "utf-8");

    const dims = loadSweepFile(filePath);
    expect(dims.length).toBe(2);
    expect(dims[0].name).toBe("threshold");
    expect(dims[0].scale).toBe("linear");
    expect(dims[1].name).toBe("strategy");
    expect(dims[1].scale).toBe("categorical");
    expect(dims[1].values).toEqual(["aggressive", "balanced"]);
  });

  it("supports log scale in sweep file", () => {
    tmpDir = mkdtempSync(join(tmpdir(), "sweep-dsl-"));
    const config = {
      dimensions: [
        { name: "lr", min: 0.001, max: 1.0, steps: 5, scale: "log" },
      ],
    };
    writeFileSync(join(tmpDir, "log.json"), JSON.stringify(config), "utf-8");

    const dims = loadSweepFile(join(tmpDir, "log.json"));
    expect(dims[0].scale).toBe("log");
    expect(dims[0].values.length).toBe(5);
  });

  it("throws for nonexistent file", () => {
    expect(() => loadSweepFile("/nonexistent/sweep.json")).toThrow();
  });

  it("throws for malformed linear dimensions instead of collapsing to a one-point sweep", () => {
    tmpDir = mkdtempSync(join(tmpdir(), "sweep-dsl-"));
    const config = {
      dimensions: [
        { name: "threshold", min: 0.3, max: 0.9 },
      ],
    };
    const filePath = join(tmpDir, "bad-linear.json");
    writeFileSync(filePath, JSON.stringify(config), "utf-8");

    expect(() => loadSweepFile(filePath)).toThrow(/Invalid linear sweep dimension/);
  });

  it("throws for malformed log dimensions instead of silently degrading", () => {
    tmpDir = mkdtempSync(join(tmpdir(), "sweep-dsl-"));
    const config = {
      dimensions: [
        { name: "lr", min: 0.001, max: 1.0, scale: "log" },
      ],
    };
    const filePath = join(tmpDir, "bad-log.json");
    writeFileSync(filePath, JSON.stringify(config), "utf-8");

    expect(() => loadSweepFile(filePath)).toThrow(/Invalid log sweep dimension/);
  });
});

// ---------------------------------------------------------------------------
// Presets
// ---------------------------------------------------------------------------

describe("parsePreset", () => {
  it("parses named presets from a JSON string", () => {
    const presets = {
      aggressive: { threshold: 0.3, budget: 500 },
      conservative: { threshold: 0.8, budget: 100 },
    };
    const result = parsePreset("aggressive", JSON.stringify(presets));
    expect(result).toEqual({ threshold: 0.3, budget: 500 });
  });

  it("returns null for unknown preset", () => {
    const result = parsePreset("unknown", JSON.stringify({ a: { x: 1 } }));
    expect(result).toBeNull();
  });

  it("returns null for invalid JSON", () => {
    const result = parsePreset("test", "not json");
    expect(result).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Extended SweepDimension type
// ---------------------------------------------------------------------------

describe("SweepDimension type", () => {
  it("has scale field", () => {
    const dims = parseSweepSpec("x=1:5:1");
    const dim: SweepDimension = dims[0];
    expect(dim).toHaveProperty("scale");
    expect(["linear", "log", "categorical"]).toContain(dim.scale);
  });

  it("supports mixed number and string values", () => {
    const dims = parseSweepSpec("count=1:3:1,mode=a,b,c");
    expect(typeof dims[0].values[0]).toBe("number");
    expect(typeof dims[1].values[0]).toBe("string");
  });
});
