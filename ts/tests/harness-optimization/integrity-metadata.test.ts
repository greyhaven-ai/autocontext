import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { validateIntegrityMetadata } from "../../src/harness-optimization/contract/validators.js";

const FIX = join(import.meta.dirname, "../../../fixtures/harness-optimization/integrity-metadata");

describe("integrity-metadata contract", () => {
  it("accepts a verified clean record", () => {
    const data = JSON.parse(readFileSync(join(FIX, "valid-verified-clean.json"), "utf8"));
    expect(validateIntegrityMetadata(data)).toEqual({ valid: true });
  });
  it("rejects a record missing mode", () => {
    const data = JSON.parse(readFileSync(join(FIX, "invalid-missing-mode.json"), "utf8"));
    const r = validateIntegrityMetadata(data);
    expect(r.valid).toBe(false);
  });
});
