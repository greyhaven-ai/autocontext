import { describe, test, expect } from "vitest";
import { EXIT } from "../../../src/control-plane/cli/_shared/exit-codes.js";

describe("EXIT codes (spec §6.5 — CI exit-code contract)", () => {
  test("success codes per spec", () => {
    expect(EXIT.PASS_STRONG_OR_MODERATE).toBe(0);
    expect(EXIT.HARD_FAIL).toBe(1);
    expect(EXIT.MARGINAL).toBe(2);
  });

  test("system errors start at 10 and are distinct", () => {
    expect(EXIT.LOCK_TIMEOUT).toBe(10);
    expect(EXIT.MISSING_BASELINE).toBe(11);
    expect(EXIT.INVALID_ARTIFACT).toBe(12);
    expect(EXIT.SCHEMA_VERSION_MISMATCH).toBe(13);

    const vals = Object.values(EXIT);
    expect(new Set(vals).size).toBe(vals.length);
  });

  test("exit codes are plain number literals", () => {
    for (const v of Object.values(EXIT)) {
      expect(typeof v).toBe("number");
      expect(Number.isInteger(v)).toBe(true);
    }
  });
});
