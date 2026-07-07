import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { validateCalibrationReport } from "../../src/harness-optimization/contract/validators.js";

const FIX = join(import.meta.dirname, "../../../fixtures/harness-optimization/calibration-report");

describe("calibration-report contract", () => {
  it("accepts a full valid report", () => {
    const data = JSON.parse(readFileSync(join(FIX, "valid-report.json"), "utf8"));
    expect(validateCalibrationReport(data)).toEqual({ valid: true });
  });
  it("rejects a report missing standard_error", () => {
    const data = JSON.parse(readFileSync(join(FIX, "invalid-missing-standard-error.json"), "utf8"));
    const r = validateCalibrationReport(data);
    expect(r.valid).toBe(false);
  });
});
