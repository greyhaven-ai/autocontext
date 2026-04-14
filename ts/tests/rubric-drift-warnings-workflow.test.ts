import { describe, expect, it } from "vitest";

import { computeRubricSnapshot } from "../src/analytics/rubric-drift-statistics.js";
import {
  DEFAULT_THRESHOLDS,
  detectRubricDrift,
  makeWarning,
} from "../src/analytics/rubric-drift-warnings.js";

describe("rubric drift warnings workflow", () => {
  it("builds warning payloads with affected scenario/provider/release metadata", () => {
    const snapshot = computeRubricSnapshot([
      { scenario: "grid_ctf", bestScore: 0.98, createdAt: "2026-01-01T00:00:00Z" },
    ], { release: "0.3.7", scenarioFamily: "game", agentProvider: "anthropic" });

    const warning = makeWarning(
      "2026-01-02T00:00:00Z",
      "perfect_rate_high",
      "high",
      "too many perfect scores",
      snapshot,
      "perfect_score_rate",
      1,
      0.5,
    );

    expect(warning).toMatchObject({
      warningType: "perfect_rate_high",
      affectedScenarios: ["grid_ctf"],
      affectedProviders: ["anthropic"],
      affectedReleases: ["0.3.7"],
    });
  });

  it("detects within-window and baseline drift warnings", () => {
    const baseline = computeRubricSnapshot([
      { scenario: "grid_ctf", bestScore: 0.5, createdAt: "2026-01-01T00:00:00Z" },
      { scenario: "grid_ctf", bestScore: 0.55, createdAt: "2026-01-02T00:00:00Z" },
    ]);
    const current = computeRubricSnapshot([
      { scenario: "grid_ctf", bestScore: 0.7, createdAt: "2026-02-01T00:00:00Z", totalGenerations: 1 },
      { scenario: "grid_ctf", bestScore: 0.98, createdAt: "2026-02-02T00:00:00Z", totalGenerations: 1 },
      { scenario: "grid_ctf", bestScore: 0.99, createdAt: "2026-02-03T00:00:00Z", totalGenerations: 1 },
    ]);

    const warnings = detectRubricDrift(current, DEFAULT_THRESHOLDS, baseline);
    expect(warnings.some((warning) => warning.metricName === "score_inflation_rate")).toBe(true);
    expect(warnings.some((warning) => warning.metricName === "mean_score_delta")).toBe(true);
    expect(warnings.some((warning) => warning.metricName === "perfect_score_rate")).toBe(true);
  });
});
