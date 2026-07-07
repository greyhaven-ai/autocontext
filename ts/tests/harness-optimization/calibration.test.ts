import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect } from "vitest";
import {
  computeCalibration,
  renderCalibrationReport,
  citeMarginVsNoise,
} from "../../src/harness-optimization/calibration.js";

// Load the SAME repo-root fixture the Python suite loads. Matching it in both
// languages is the load-bearing parity proof.
// Walk up to the repo root: ts/tests/harness-optimization/ -> ts/tests/ -> ts/ -> <repo root>.
const CASES = JSON.parse(
  readFileSync(
    join(
      import.meta.dirname,
      "..",
      "..",
      "..",
      "fixtures",
      "harness-optimization",
      "calibration-cases",
      "calibration-cases.json",
    ),
    "utf8",
  ),
).cases;

const TOL = 1e-9;

describe("calibration parity", () => {
  for (const c of CASES) {
    it(`${c.name}: numeric + enum fields match fixture within ${TOL}`, () => {
      const report = computeCalibration(c.scores, {
        scenarioId: c.scenario_id,
        currentMinDelta: c.current_min_delta,
        maxTrials: c.max_trials,
      });
      const e = c.expected;
      expect(report.sample_size).toBe(e.sample_size);
      expect(Math.abs(report.mean - e.mean)).toBeLessThanOrEqual(TOL);
      expect(Math.abs(report.variance - e.variance)).toBeLessThanOrEqual(TOL);
      expect(Math.abs(report.std_dev - e.std_dev)).toBeLessThanOrEqual(TOL);
      expect(Math.abs(report.standard_error - e.standard_error)).toBeLessThanOrEqual(TOL);
      expect(Math.abs(report.recommended_min_delta - e.recommended_min_delta)).toBeLessThanOrEqual(
        TOL,
      );
      expect(report.recommended_trial_count).toBe(e.recommended_trial_count);
      expect(Math.abs(report.current_min_delta - c.current_min_delta)).toBeLessThanOrEqual(TOL);
      expect(report.margin_vs_noise).toBe(e.margin_vs_noise);
      expect(report.sparse_metric_too_noisy).toBe(e.sparse_metric_too_noisy);
    });
  }

  it("renders the high_noise report character-for-character including the sparse-noise line", () => {
    const c = CASES.find((x: { name: string }) => x.name === "high_noise");
    const report = computeCalibration(c.scores, {
      scenarioId: c.scenario_id,
      currentMinDelta: c.current_min_delta,
      maxTrials: c.max_trials,
    });
    const expected = [
      `calibration report: ${report.scenario_id}`,
      `samples: ${report.sample_size}`,
      `mean: ${report.mean.toFixed(6)}`,
      `std_dev: ${report.std_dev.toFixed(6)}`,
      `standard_error: ${report.standard_error.toFixed(6)}`,
      `recommended_min_delta: ${report.recommended_min_delta.toFixed(6)}`,
      `recommended_trial_count: ${report.recommended_trial_count}`,
      `current_min_delta: ${report.current_min_delta.toFixed(6)}`,
      `margin: ${report.margin_vs_noise}`,
      "sparse metric too noisy: optimize a denser verifier signal instead",
    ].join("\n");
    expect(renderCalibrationReport(report)).toBe(expected);
    expect(report.sparse_metric_too_noisy).toBe(true);
  });

  it("omits the sparse-noise line for the low_noise report", () => {
    const c = CASES.find((x: { name: string }) => x.name === "low_noise");
    const report = computeCalibration(c.scores, {
      scenarioId: c.scenario_id,
      currentMinDelta: c.current_min_delta,
      maxTrials: c.max_trials,
    });
    const rendered = renderCalibrationReport(report);
    expect(rendered).not.toContain("sparse metric too noisy");
    expect(rendered.endsWith(`margin: ${report.margin_vs_noise}`)).toBe(true);
  });

  it("cites the margin against the noise floor on one line", () => {
    const c = CASES.find((x: { name: string }) => x.name === "high_noise");
    const report = computeCalibration(c.scores, {
      scenarioId: c.scenario_id,
      currentMinDelta: c.current_min_delta,
      maxTrials: c.max_trials,
    });
    const expected = `margin ${report.current_min_delta.toFixed(6)} is ${report.margin_vs_noise} (recommended >= ${report.recommended_min_delta.toFixed(6)})`;
    expect(citeMarginVsNoise(report)).toBe(expected);
  });
});
