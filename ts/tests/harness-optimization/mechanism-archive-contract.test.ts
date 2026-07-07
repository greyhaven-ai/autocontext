import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  validateFrontierMechanism,
  validateOrphanMechanism,
} from "../../src/harness-optimization/contract/validators.js";

const FIX = join(import.meta.dirname, "../../../fixtures/harness-optimization/mechanism-archive");

describe("mechanism-archive contract", () => {
  it("accepts a valid frontier mechanism", () => {
    const data = JSON.parse(readFileSync(join(FIX, "valid-frontier.json"), "utf8"));
    expect(validateFrontierMechanism(data)).toEqual({ valid: true });
  });

  it("accepts a valid orphan mechanism", () => {
    const data = JSON.parse(readFileSync(join(FIX, "valid-orphan.json"), "utf8"));
    expect(validateOrphanMechanism(data)).toEqual({ valid: true });
  });

  it("rejects an orphan missing failure_family", () => {
    const data = JSON.parse(
      readFileSync(join(FIX, "invalid-orphan-missing-failure-family.json"), "utf8"),
    );
    const r = validateOrphanMechanism(data);
    expect(r.valid).toBe(false);
  });
});
