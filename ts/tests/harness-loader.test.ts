import { describe, expect, it } from "vitest";

import {
  HarnessLoader,
  HarnessSpecSchema,
  HarnessValidationResultSchema,
  parseArchitectHarnessSpecs,
  type ValidatorFn,
} from "../src/execution/harness-loader.js";

// ── Schema tests ─────────────────────────────────────────────────────────────

describe("HarnessValidationResultSchema", () => {
  it("validates a passing result", () => {
    const result = HarnessValidationResultSchema.parse({
      passed: true,
      errors: [],
    });
    expect(result.passed).toBe(true);
    expect(result.validatorName).toBe("");
  });

  it("validates a failing result", () => {
    const result = HarnessValidationResultSchema.parse({
      passed: false,
      errors: ["bad move"],
      validatorName: "check_moves",
    });
    expect(result.passed).toBe(false);
    expect(result.errors).toEqual(["bad move"]);
  });
});

describe("HarnessSpecSchema", () => {
  it("validates a minimal spec", () => {
    const result = HarnessSpecSchema.parse({
      name: "check",
      code: "def validate_strategy(s, sc): return True, []",
    });
    expect(result.name).toBe("check");
    expect(result.description).toBeUndefined();
  });

  it("validates a spec with description", () => {
    const result = HarnessSpecSchema.parse({
      name: "check",
      code: "x = 1",
      description: "A validator",
    });
    expect(result.description).toBe("A validator");
  });

  it("rejects missing name", () => {
    const result = HarnessSpecSchema.safeParse({ code: "x = 1" });
    expect(result.success).toBe(false);
  });
});

// ── parseArchitectHarnessSpecs tests ─────────────────────────────────────────

describe("parseArchitectHarnessSpecs", () => {
  it("extracts valid harness specs", () => {
    const content = [
      "Some text",
      "<!-- HARNESS_START -->",
      JSON.stringify({
        harness: [{ name: "check", code: "x = 1" }],
      }),
      "<!-- HARNESS_END -->",
      "More text",
    ].join("\n");

    const specs = parseArchitectHarnessSpecs(content);
    expect(specs).toHaveLength(1);
    expect(specs[0].name).toBe("check");
  });

  it("returns empty for no markers", () => {
    expect(parseArchitectHarnessSpecs("no markers")).toEqual([]);
  });

  it("returns empty for invalid JSON", () => {
    const content =
      "<!-- HARNESS_START -->\nnot json\n<!-- HARNESS_END -->";
    expect(parseArchitectHarnessSpecs(content)).toEqual([]);
  });

  it("skips entries with missing fields", () => {
    const content = [
      "<!-- HARNESS_START -->",
      JSON.stringify({ harness: [{ name: "no_code" }] }),
      "<!-- HARNESS_END -->",
    ].join("\n");
    expect(parseArchitectHarnessSpecs(content)).toEqual([]);
  });

  it("keeps valid entries when mixed with invalid", () => {
    const content = [
      "<!-- HARNESS_START -->",
      JSON.stringify({
        harness: [
          { name: "good", code: "x = 1" },
          { name: "bad" }, // missing code
        ],
      }),
      "<!-- HARNESS_END -->",
    ].join("\n");
    const specs = parseArchitectHarnessSpecs(content);
    expect(specs).toHaveLength(1);
    expect(specs[0].name).toBe("good");
  });
});

// ── HarnessLoader tests ──────────────────────────────────────────────────────

describe("HarnessLoader", () => {
  const passingValidator: ValidatorFn = () => ({
    passed: true,
    errors: [],
  });

  const failingValidator: ValidatorFn = () => ({
    passed: false,
    errors: ["invalid move"],
  });

  it("passes with no validators", () => {
    const loader = new HarnessLoader();
    const result = loader.validateStrategy({}, null);
    expect(result.passed).toBe(true);
  });

  it("passes when all validators pass", () => {
    const loader = new HarnessLoader();
    loader.register("a", passingValidator);
    loader.register("b", passingValidator);
    const result = loader.validateStrategy({}, null);
    expect(result.passed).toBe(true);
  });

  it("fails when a validator fails", () => {
    const loader = new HarnessLoader();
    loader.register("a", passingValidator);
    loader.register("b", failingValidator);
    const result = loader.validateStrategy({}, null);
    expect(result.passed).toBe(false);
    expect(result.errors.some((e) => e.includes("[b]"))).toBe(true);
  });

  it("captures validator exceptions", () => {
    const loader = new HarnessLoader();
    loader.register("boom", () => {
      throw new Error("kaboom");
    });
    const result = loader.validateStrategy({}, null);
    expect(result.passed).toBe(false);
    expect(result.errors.some((e) => e.includes("kaboom"))).toBe(true);
  });

  it("unregisters validators", () => {
    const loader = new HarnessLoader();
    loader.register("a", failingValidator);
    expect(loader.has("a")).toBe(true);
    loader.unregister("a");
    expect(loader.has("a")).toBe(false);
    const result = loader.validateStrategy({}, null);
    expect(result.passed).toBe(true);
  });

  it("returns registered names", () => {
    const loader = new HarnessLoader();
    loader.register("alpha", passingValidator);
    loader.register("beta", passingValidator);
    expect(loader.registeredNames.sort()).toEqual(["alpha", "beta"]);
  });
});
