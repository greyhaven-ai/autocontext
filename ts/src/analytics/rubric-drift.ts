/**
 * Rubric-drift monitoring for score inflation and stability detection.
 *
 * TS port of autocontext.analytics.rubric_drift (AC-381).
 */

import {
  computeRubricSnapshot,
  syntheticTimestamp,
} from "./rubric-drift-statistics.js";
import {
  DEFAULT_THRESHOLDS,
  detectRubricDrift,
} from "./rubric-drift-warnings.js";
import type {
  DriftReport,
  DriftThresholds,
  RubricSnapshot,
  RunFacetLike,
} from "./rubric-drift-types.js";

export type {
  DelightSignalLike,
  DriftReport,
  DriftThresholds,
  DriftWarning,
  RubricSnapshot,
  RunFacetLike,
} from "./rubric-drift-types.js";

export class RubricDriftMonitor {
  readonly #thresholds: DriftThresholds;
  readonly #recordedFacets: RunFacetLike[] = [];

  constructor(thresholds: Partial<DriftThresholds> = {}) {
    this.#thresholds = { ...DEFAULT_THRESHOLDS, ...thresholds };
  }

  recordScore(score: number): void {
    this.#recordedFacets.push({
      scenario: "",
      bestScore: score,
      createdAt: syntheticTimestamp(this.#recordedFacets.length),
      totalGenerations: 1,
      delightSignals: [],
      retries: 0,
      rollbacks: 0,
    });
  }

  computeSnapshot(
    facets: readonly RunFacetLike[],
    release = "",
    scenarioFamily = "",
    agentProvider = "",
  ): RubricSnapshot {
    return computeRubricSnapshot(facets, {
      release,
      scenarioFamily,
      agentProvider,
    });
  }

  detectDrift(current: RubricSnapshot, baseline?: RubricSnapshot) {
    return detectRubricDrift(current, this.#thresholds, baseline);
  }

  analyze(
    facets: readonly RunFacetLike[] = this.#recordedFacets,
    options: {
      release?: string;
      scenarioFamily?: string;
      agentProvider?: string;
      baseline?: RubricSnapshot;
    } = {},
  ): DriftReport {
    const snapshot = this.computeSnapshot(
      facets,
      options.release ?? "",
      options.scenarioFamily ?? "",
      options.agentProvider ?? "",
    );
    const warnings = this.detectDrift(snapshot, options.baseline);
    return {
      snapshot,
      warnings,
      stable: warnings.length === 0,
      meanScore: snapshot.meanScore,
      scoreCount: snapshot.runCount,
    };
  }
}
