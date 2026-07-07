import type { CalibrationReport } from "./contract/generated-types.js";

/**
 * Noise calibration engine for the harness optimization loop (AC-881).
 *
 * TypeScript half of the AC-881 parity pair: the same pure functions live here
 * and in `autocontext/src/autocontext/harness_optimization/calibration.py`, and
 * a shared fixture
 * (`fixtures/harness-optimization/calibration-cases/calibration-cases.json`)
 * proves both languages compute identical statistics to 1e-9 and render
 * character-identical text.
 *
 * The float formulas here are mirrored by the Python engine and must agree to
 * 1e-9, so the arithmetic order below is intentional and should not be changed.
 */

export interface CalibrationOptions {
  scenarioId: string;
  currentMinDelta: number;
  maxTrials: number;
  noiseMultiplier?: number;
  noisyCvThreshold?: number;
}

/**
 * Compute noise statistics and gating recommendations for a score series.
 *
 * Mirrors Python `compute_calibration`: sample mean, ddof=1 variance, standard
 * error, a noise-multiplier margin, and a budget-capped trial count, plus the
 * margin-vs-noise verdict and the sparse-metric coefficient-of-variation flag.
 */
export function computeCalibration(
  scores: readonly number[],
  opts: CalibrationOptions,
): CalibrationReport {
  const noiseMultiplier = opts.noiseMultiplier ?? 2.0;
  const noisyCvThreshold = opts.noisyCvThreshold ?? 0.25;

  const n = scores.length;
  const mean = n > 0 ? scores.reduce((acc, x) => acc + x, 0) / n : 0;
  const variance = n >= 2 ? scores.reduce((acc, x) => acc + (x - mean) ** 2, 0) / (n - 1) : 0;
  const stdDev = Math.sqrt(variance);
  const standardError = n >= 2 ? stdDev / Math.sqrt(n) : 0;
  const recommendedMinDelta = noiseMultiplier * standardError;

  let k: number;
  if (opts.currentMinDelta > 0 && stdDev > 0) {
    k = Math.ceil((stdDev / opts.currentMinDelta) ** 2);
  } else {
    k = opts.maxTrials;
  }
  const recommendedTrialCount = Math.max(1, Math.min(k, opts.maxTrials));

  const marginVsNoise: "above_noise" | "below_noise" =
    opts.currentMinDelta >= recommendedMinDelta ? "above_noise" : "below_noise";

  let sparseMetricTooNoisy: boolean;
  if (Math.abs(mean) > 0) {
    sparseMetricTooNoisy = standardError / Math.abs(mean) > noisyCvThreshold;
  } else {
    sparseMetricTooNoisy = standardError > 0;
  }

  const notes = `SE=${standardError.toFixed(4)} over n=${n}; margin ${marginVsNoise}`;

  return {
    schema_version: 1,
    scenario_id: opts.scenarioId,
    sample_size: n,
    mean,
    variance,
    std_dev: stdDev,
    standard_error: standardError,
    recommended_min_delta: recommendedMinDelta,
    recommended_trial_count: recommendedTrialCount,
    current_min_delta: opts.currentMinDelta,
    margin_vs_noise: marginVsNoise,
    sparse_metric_too_noisy: sparseMetricTooNoisy,
    notes,
  };
}

// Numeric fields are rendered with a fixed 6-decimal format so the text is
// identical across languages. Do NOT use raw String(): JS String(2) -> "2"
// while Python str(2.0) -> "2.0", which would diverge. This matches Python's
// _fmt_float (f"{value:.6f}") character-for-character.
function fmtFloat(value: number): string {
  return value.toFixed(6);
}

const SPARSE_NOISE_LINE = "sparse metric too noisy: optimize a denser verifier signal instead";

/**
 * Render a calibration report as a stable multi-line string. Mirrors Python
 * `render_calibration_report` character-for-character: float fields through
 * `fmtFloat` (6-decimal fixed) and plain String() for the integer counts.
 */
export function renderCalibrationReport(report: CalibrationReport): string {
  const lines = [
    `calibration report: ${report.scenario_id}`,
    `samples: ${report.sample_size}`,
    `mean: ${fmtFloat(report.mean)}`,
    `std_dev: ${fmtFloat(report.std_dev)}`,
    `standard_error: ${fmtFloat(report.standard_error)}`,
    `recommended_min_delta: ${fmtFloat(report.recommended_min_delta)}`,
    `recommended_trial_count: ${String(report.recommended_trial_count)}`,
    `current_min_delta: ${fmtFloat(report.current_min_delta)}`,
    `margin: ${report.margin_vs_noise}`,
  ];
  if (report.sparse_metric_too_noisy) {
    lines.push(SPARSE_NOISE_LINE);
  }
  return lines.join("\n");
}

/**
 * Render a one-line citation of the margin against the noise floor. Both
 * numbers use the same 6-decimal fixed format as `renderCalibrationReport` so
 * the Python port (f"{value:.6f}") produces identical text.
 */
export function citeMarginVsNoise(report: CalibrationReport): string {
  return `margin ${fmtFloat(report.current_min_delta)} is ${report.margin_vs_noise} (recommended >= ${fmtFloat(report.recommended_min_delta)})`;
}
