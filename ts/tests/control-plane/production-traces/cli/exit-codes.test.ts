import { describe, test, expect } from "vitest";
import { EXIT } from "../../../../src/production-traces/cli/_shared/exit-codes.js";

describe("production-traces EXIT codes (spec §9.7)", () => {
  test("success codes", () => {
    expect(EXIT.SUCCESS).toBe(0);
    expect(EXIT.DOMAIN_FAILURE).toBe(1);
    expect(EXIT.PARTIAL_SUCCESS).toBe(2);
  });

  test("system errors start at 10 per spec §9.7", () => {
    expect(EXIT.LOCK_TIMEOUT).toBe(10);
    expect(EXIT.INVALID_CONFIG).toBe(11);
    expect(EXIT.NO_MATCHING_TRACES).toBe(12);
    expect(EXIT.SCHEMA_VERSION_MISMATCH).toBe(13);
    expect(EXIT.IO_FAILURE).toBe(14);
  });

  test("all exit codes are distinct integers", () => {
    const vals = Object.values(EXIT);
    for (const v of vals) {
      expect(typeof v).toBe("number");
      expect(Number.isInteger(v)).toBe(true);
    }
    expect(new Set(vals).size).toBe(vals.length);
  });

  test("shape mirrors Foundation B table (0, 1, 2, 10+)", () => {
    // Contract: low band = decision outcomes; high band = system faults.
    const decisionVals = [EXIT.SUCCESS, EXIT.DOMAIN_FAILURE, EXIT.PARTIAL_SUCCESS];
    const systemVals = [
      EXIT.LOCK_TIMEOUT,
      EXIT.INVALID_CONFIG,
      EXIT.NO_MATCHING_TRACES,
      EXIT.SCHEMA_VERSION_MISMATCH,
      EXIT.IO_FAILURE,
    ];
    for (const v of decisionVals) expect(v).toBeLessThan(10);
    for (const v of systemVals) expect(v).toBeGreaterThanOrEqual(10);
  });
});
