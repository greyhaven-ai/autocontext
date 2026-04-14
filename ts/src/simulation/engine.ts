/**
 * Simulation engine — first-class `simulate` surface (AC-446).
 *
 * Takes a plain-language description, builds a simulation spec via LLM,
 * executes one or more trajectories (optionally across a sweep grid),
 * and returns structured findings with assumptions and warnings.
 *
 * Built on top of existing scenario families (simulation, operator_loop)
 * and the codegen/materialization pipeline.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import type { ScenarioFamilyName } from "../scenarios/families.js";
import { SIMULATION_LIKE_FAMILIES } from "../scenarios/families.js";
import {
  buildSimulationExecutionConfig,
  collectReplayVariables,
  deriveSimulationName,
  inferSimulationFamily,
  resolveSimulationExecutionConfig,
} from "./request-planner.js";
import { loadSimulationReport, persistSimulationArtifacts } from "./artifact-store.js";
import {
  executeSimulationFamily,
  loadGeneratedSimulationScenario,
} from "./family-executor.js";
import {
  buildSimulationVariant,
  loadReplaySimulationVariant,
} from "./variant-materializer.js";
import {
  aggregateSimulationRuns,
  aggregateSimulationSweep,
  buildSimulationAssumptions,
  buildSimulationWarnings,
  DEGRADED_SCORE_THRESHOLD,
  deriveSimulationStatus,
} from "./summary.js";
import {
  normalizeSimulationDelta,
  normalizeSimulationScore,
} from "./score-normalization.js";
import type {
  CompareRequest,
  ReplayRequest,
  SimulationCompareResult,
  SimulationExecutionConfig,
  SimulationRequest,
  SimulationResult,
  SimulationSummary,
  SweepResult,
  VariableDelta,
} from "./types.js";

// SweepDimension is now defined in sweep-dsl.ts (AC-454)
import type { SweepDimension } from "./sweep-dsl.js";
export type { SweepDimension } from "./sweep-dsl.js";
export type {
  CompareRequest,
  ReplayRequest,
  SimulationCompareResult,
  SimulationExecutionConfig,
  SimulationRequest,
  SimulationResult,
  SimulationSummary,
  SimulationStatus,
  SweepResult,
  VariableDelta,
} from "./types.js";
export { DEGRADED_SCORE_THRESHOLD, deriveSimulationStatus } from "./summary.js";

// ---------------------------------------------------------------------------
// Parsing helpers
// ---------------------------------------------------------------------------

/**
 * Parse variable overrides from CLI flag: "key=val,key2=val2"
 */
export function parseVariableOverrides(input: string): Record<string, unknown> {
  if (!input.trim()) return {};
  const vars: Record<string, unknown> = {};
  for (const pair of input.split(",")) {
    const [key, ...rest] = pair.split("=");
    const val = rest.join("=");
    if (!key?.trim()) continue;
    const num = Number(val);
    vars[key.trim()] = isNaN(num) || val.trim() === "" ? val.trim() : num;
  }
  return vars;
}

/**
 * Parse sweep spec from CLI flag.
 *
 * Delegates to the rich sweep DSL (AC-454) which supports:
 * - Linear: key=min:max:step
 * - Logarithmic: key=log:min:max:steps
 * - Categorical: key=val1,val2,val3
 *
 * @see sweep-dsl.ts for full documentation
 */
export { parseSweepSpec } from "./sweep-dsl.js";

// ---------------------------------------------------------------------------
// Engine
// ---------------------------------------------------------------------------

function generateId(): string {
  return `sim_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/** Re-export for backward compatibility — canonical source is families.ts */
export { SIMULATION_LIKE_FAMILIES as SIMULATION_FAMILIES };

export class SimulationEngine {
  #provider: LLMProvider;
  #knowledgeRoot: string;

  constructor(provider: LLMProvider, knowledgeRoot: string) {
    this.#provider = provider;
    this.#knowledgeRoot = knowledgeRoot;
  }

  /**
   * Run a simulation from a plain-language description.
   */
  async run(request: SimulationRequest): Promise<SimulationResult> {
    const id = generateId();
    const name = request.saveAs ?? deriveSimulationName(request.description);
    const family = inferSimulationFamily(request.description) as ScenarioFamilyName;
    const scenarioDir = join(this.#knowledgeRoot, "_simulations", name);
    const execution = buildSimulationExecutionConfig(request);

    try {
      const baseVariant = await buildSimulationVariant({
        provider: this.#provider,
        description: request.description,
        family,
        name,
        variables: request.variables,
      });
      persistSimulationArtifacts({
        knowledgeRoot: this.#knowledgeRoot,
        name,
        family,
        spec: baseVariant.spec,
        source: baseVariant.source,
        scenarioDir,
      });

      // Execute — single or sweep
      let summary: SimulationSummary;
      let sweepResult: SweepResult | undefined;

      if (request.sweep && request.sweep.length > 0) {
        const sweepData = await this.#executeSweep(
          request.description,
          family,
          name,
          request,
          scenarioDir,
        );
        sweepResult = sweepData;
        summary = aggregateSimulationSweep(sweepData);
      } else {
        const results = await this.#executeRuns(
          baseVariant.source,
          family,
          name,
          execution.runs,
          execution.maxSteps,
        );
        summary = aggregateSimulationRuns(results);
      }

      const assumptions = buildSimulationAssumptions(
        baseVariant.spec,
        family,
        request.variables,
      );
      const warnings = buildSimulationWarnings(family, this.#provider.name);

      const reportPath = join(scenarioDir, "report.json");
      const resultObj: SimulationResult = {
        id,
        name,
        family,
        status: deriveSimulationStatus(summary.score),
        description: request.description,
        assumptions,
        variables: request.variables ?? {},
        sweep: sweepResult,
        summary,
        execution,
        artifacts: { scenarioDir, reportPath },
        warnings,
      };
      writeFileSync(reportPath, JSON.stringify(resultObj, null, 2), "utf-8");

      return resultObj;
    } catch (err) {
      return {
        id,
        name,
        family,
        status: "failed",
        description: request.description,
        assumptions: [],
        variables: request.variables ?? {},
        summary: { score: 0, reasoning: "", dimensionScores: {} },
        execution,
        artifacts: { scenarioDir: "" },
        warnings: [],
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }

  /**
   * Replay a previously saved simulation (AC-450).
   *
   * Loads the saved spec + generated code from artifacts, re-executes
   * with the same (or optionally modified) parameters, and returns
   * a result with comparison data against the original.
   */
  async replay(request: ReplayRequest): Promise<SimulationResult> {
    const id = generateId();
    const name = request.id;
    const scenarioDir = join(this.#knowledgeRoot, "_simulations", name);

    // Load saved report
    const reportPath = join(scenarioDir, "report.json");
    if (!existsSync(reportPath)) {
      return {
        id,
        name,
        family: "simulation",
        status: "failed",
        description: "",
        assumptions: [],
        variables: {},
        summary: { score: 0, reasoning: "", dimensionScores: {} },
        artifacts: { scenarioDir: "" },
        warnings: [],
        error: `Simulation '${name}' not found at ${scenarioDir}`,
      };
    }

    const originalReport = JSON.parse(
      readFileSync(reportPath, "utf-8"),
    ) as SimulationResult;
    const originalScore = originalReport.summary?.score ?? 0;
    const family = (originalReport.family ??
      "simulation") as ScenarioFamilyName;
    const execution = resolveSimulationExecutionConfig(originalReport);
    const replayMaxSteps = request.maxSteps ?? execution.maxSteps;

    try {
      let summary: SimulationSummary;
      let sweepResult: SweepResult | undefined;
      let variables: Record<string, unknown>;

      if (execution.sweep && execution.sweep.length > 0) {
        const replayedSweep = await this.#replaySweep(
          scenarioDir,
          family,
          name,
          execution,
          originalReport,
          request.variables,
          replayMaxSteps,
        );
        sweepResult = replayedSweep;
        summary = aggregateSimulationSweep(replayedSweep);
        variables = collectReplayVariables(originalReport, request.variables);
      } else {
        const variant = await loadReplaySimulationVariant({
          scenarioDir,
          family,
          name,
          variables: collectReplayVariables(originalReport, request.variables),
          regenerate: Object.keys(request.variables ?? {}).length > 0,
        });
        variables = variant.variables;
        const results = await this.#executeRuns(
          variant.source,
          family,
          name,
          execution.runs,
          replayMaxSteps,
        );
        summary = aggregateSimulationRuns(results);
      }

      const replayReportPath = join(scenarioDir, `replay_${id}.json`);
      const result: SimulationResult = {
        id,
        name,
        family,
        status: deriveSimulationStatus(summary.score),
        description: originalReport.description ?? "",
        assumptions: originalReport.assumptions ?? [],
        variables,
        sweep: sweepResult,
        summary,
        execution: {
          runs: execution.runs,
          maxSteps: replayMaxSteps,
          sweep: execution.sweep,
        },
        artifacts: { scenarioDir, reportPath: replayReportPath },
        warnings: [
          ...(originalReport.warnings ?? []),
          "This is a replay of a previously saved simulation.",
        ],
        replayOf: name,
        originalScore,
        scoreDelta: normalizeSimulationDelta(summary.score - originalScore),
      };

      writeFileSync(replayReportPath, JSON.stringify(result, null, 2), "utf-8");
      return result;
    } catch (err) {
      return {
        id,
        name,
        family,
        status: "failed",
        description: originalReport.description ?? "",
        assumptions: [],
        variables: collectReplayVariables(originalReport, request.variables),
        summary: { score: 0, reasoning: "", dimensionScores: {} },
        artifacts: { scenarioDir },
        warnings: [],
        error: err instanceof Error ? err.message : String(err),
        replayOf: name,
      };
    }
  }

  // -------------------------------------------------------------------------
  /**
   * Compare two saved simulations (AC-451).
   *
   * Loads both reports, computes score/variable/dimension deltas,
   * and identifies likely drivers of outcome differences.
   */
  async compare(request: CompareRequest): Promise<SimulationCompareResult> {
    const leftReport = loadSimulationReport(this.#knowledgeRoot, request.left);
    const rightReport = loadSimulationReport(this.#knowledgeRoot, request.right);

    if (!leftReport || !rightReport) {
      const missing = !leftReport ? request.left : request.right;
      return {
        status: "failed",
        left: { name: request.left, score: 0, variables: {} },
        right: { name: request.right, score: 0, variables: {} },
        scoreDelta: 0,
        variableDeltas: {},
        dimensionDeltas: {},
        likelyDrivers: [],
        summary: "",
        error: `Simulation '${missing}' not found`,
      };
    }

    if (leftReport.family !== rightReport.family) {
      return {
        status: "failed",
        left: {
          name: request.left,
          score: leftReport.summary?.score ?? 0,
          variables: (leftReport.variables ?? {}) as Record<string, unknown>,
        },
        right: {
          name: request.right,
          score: rightReport.summary?.score ?? 0,
          variables: (rightReport.variables ?? {}) as Record<string, unknown>,
        },
        scoreDelta: 0,
        variableDeltas: {},
        dimensionDeltas: {},
        likelyDrivers: [],
        summary: "",
        error: `Cannot compare simulations across different families (${leftReport.family} vs ${rightReport.family})`,
      };
    }

    const leftScore = leftReport.summary?.score ?? 0;
    const rightScore = rightReport.summary?.score ?? 0;
    const scoreDelta = normalizeSimulationDelta(rightScore - leftScore);

    // Variable deltas
    const leftVars = this.#collectCompareVariables(leftReport);
    const rightVars = this.#collectCompareVariables(rightReport);
    const allVarKeys = new Set([
      ...Object.keys(leftVars),
      ...Object.keys(rightVars),
    ]);
    const variableDeltas: Record<string, VariableDelta> = {};
    for (const key of allVarKeys) {
      const lv = leftVars[key];
      const rv = rightVars[key];
      const delta =
        typeof lv === "number" && typeof rv === "number"
          ? normalizeSimulationDelta(rv - lv)
          : undefined;
      variableDeltas[key] = { left: lv, right: rv, delta };
    }

    // Dimension deltas
    const leftDims = (leftReport.summary?.dimensionScores ?? {}) as Record<
      string,
      number
    >;
    const rightDims = (rightReport.summary?.dimensionScores ?? {}) as Record<
      string,
      number
    >;
    const allDimKeys = new Set([
      ...Object.keys(leftDims),
      ...Object.keys(rightDims),
    ]);
    const dimensionDeltas: Record<
      string,
      { left: number; right: number; delta: number }
    > = {};
    for (const key of allDimKeys) {
      const lv = leftDims[key] ?? 0;
      const rv = rightDims[key] ?? 0;
      dimensionDeltas[key] = {
        left: normalizeSimulationScore(lv),
        right: normalizeSimulationScore(rv),
        delta: normalizeSimulationDelta(rv - lv),
      };
    }

    // Likely drivers: variables that changed AND where dimensions shifted
    const likelyDrivers: string[] = [];
    for (const [key, vd] of Object.entries(variableDeltas)) {
      if (!this.#valuesEqual(vd.left, vd.right)) {
        likelyDrivers.push(key);
      }
    }
    // Also add dimensions with large changes
    for (const [key, dd] of Object.entries(dimensionDeltas)) {
      if (Math.abs(dd.delta) > 0.05 && !likelyDrivers.includes(key)) {
        likelyDrivers.push(key);
      }
    }

    // Summary
    const direction =
      scoreDelta > 0 ? "improved" : scoreDelta < 0 ? "regressed" : "unchanged";
    const summary =
      `Score ${direction} by ${Math.abs(scoreDelta).toFixed(4)} ` +
      `(${leftScore.toFixed(2)} → ${rightScore.toFixed(2)}). ` +
      `${Object.keys(variableDeltas).length} variable(s) compared, ` +
      `${likelyDrivers.length} likely driver(s).`;

    // Persist report
    const reportsDir = join(this.#knowledgeRoot, "_simulations", "_comparisons");
    if (!existsSync(reportsDir)) mkdirSync(reportsDir, { recursive: true });
    const reportPath = join(
      reportsDir,
      `${request.left}_vs_${request.right}.json`,
    );
    const result: SimulationCompareResult = {
      status: deriveSimulationStatus(Math.min(leftScore, rightScore)),
      left: {
        name: request.left,
        score: leftScore,
        variables: leftVars as Record<string, unknown>,
      },
      right: {
        name: request.right,
        score: rightScore,
        variables: rightVars as Record<string, unknown>,
      },
      scoreDelta,
      variableDeltas,
      dimensionDeltas,
      likelyDrivers,
      summary,
      reportPath,
    };
    writeFileSync(reportPath, JSON.stringify(result, null, 2), "utf-8");

    return result;
  }

  #collectCompareVariables(
    report: SimulationResult,
  ): Record<string, unknown> {
    const merged: Record<string, unknown> = { ...(report.variables ?? {}) };

    if (!report.sweep?.results?.length) {
      return merged;
    }

    const valueSets = new Map<string, unknown[]>();
    for (const result of report.sweep.results) {
      for (const [key, value] of Object.entries(result.variables ?? {})) {
        const existing = valueSets.get(key) ?? [];
        if (!existing.some((entry) => this.#valuesEqual(entry, value))) {
          existing.push(value);
          valueSets.set(key, existing);
        }
      }
    }

    for (const [key, values] of valueSets.entries()) {
      if (
        key in merged &&
        values.length === 1 &&
        this.#valuesEqual(merged[key], values[0])
      ) {
        continue;
      }
      merged[key] = values.length === 1 ? values[0] : values;
    }

    return merged;
  }

  #valuesEqual(left: unknown, right: unknown): boolean {
    return JSON.stringify(left) === JSON.stringify(right);
  }

  // Internals
  // -------------------------------------------------------------------------

  #failedResult(
    id: string,
    name: string,
    family: ScenarioFamilyName,
    request: SimulationRequest,
    errors: string[],
  ): SimulationResult {
    return {
      id,
      name,
      family,
      status: "failed",
      description: request.description,
      assumptions: [],
      variables: request.variables ?? {},
      summary: { score: 0, reasoning: errors.join("; "), dimensionScores: {} },
      artifacts: { scenarioDir: "" },
      warnings: [],
      error: errors.join("; "),
    };
  }

  async #replaySweep(
    scenarioDir: string,
    family: ScenarioFamilyName,
    name: string,
    execution: SimulationExecutionConfig,
    originalReport: SimulationResult,
    overrides?: Record<string, unknown>,
    maxSteps?: number,
  ): Promise<SweepResult> {
    const originalSweep = originalReport.sweep;
    if (!originalSweep) {
      throw new Error("Saved simulation does not contain sweep metadata");
    }

    const results: SweepResult["results"] = [];
    for (let i = 0; i < originalSweep.results.length; i++) {
      const originalCell = originalSweep.results[i];
      const variantDir = join(scenarioDir, "sweep", `${i + 1}`);
      const variantName = `${name}__sweep_${i + 1}`;
      const variables = {
        ...(originalCell.variables ?? {}),
        ...(overrides ?? {}),
      };
      const regenerate = Object.keys(overrides ?? {}).length > 0;
      const variant = await loadReplaySimulationVariant({
        scenarioDir: variantDir,
        family,
        name: variantName,
        variables,
        regenerate,
      });
      const rerunResults = await this.#executeRuns(
        variant.source,
        family,
        variantName,
        execution.runs,
        maxSteps,
      );
      const aggregate = aggregateSimulationRuns(rerunResults);
      results.push({
        variables,
        score: aggregate.score,
        reasoning: aggregate.reasoning,
        dimensionScores: aggregate.dimensionScores,
      });
    }

    return {
      dimensions: execution.sweep ?? originalSweep.dimensions,
      runs: results.length * execution.runs,
      results,
    };
  }

  async #executeRuns(
    source: string,
    family: ScenarioFamilyName,
    name: string,
    runs: number,
    maxSteps?: number,
  ): Promise<
    Array<{
      score: number;
      reasoning: string;
      dimensionScores: Record<string, number>;
    }>
  > {
    const results: Array<{
      score: number;
      reasoning: string;
      dimensionScores: Record<string, number>;
    }> = [];
    for (let seed = 0; seed < runs; seed++) {
      const result = await this.#executeSingle(
        source,
        family,
        name,
        seed,
        maxSteps,
      );
      results.push(result);
    }
    return results;
  }

  async #executeSweep(
    description: string,
    family: ScenarioFamilyName,
    name: string,
    request: SimulationRequest,
    scenarioDir: string,
  ): Promise<SweepResult> {
    const dimensions = request.sweep ?? [];
    const runResults: SweepResult["results"] = [];
    const runsPerCombo = Math.max(1, request.runs ?? 1);

    const combos = this.#cartesianProduct(dimensions);
    for (let i = 0; i < combos.length; i++) {
      const variables = { ...(request.variables ?? {}), ...combos[i] };
      const variantName = `${name}__sweep_${i + 1}`;
      const variant = await buildSimulationVariant({
        provider: this.#provider,
        description,
        family,
        name: variantName,
        variables,
      });
      persistSimulationArtifacts({
        knowledgeRoot: this.#knowledgeRoot,
        name: variantName,
        family,
        spec: variant.spec,
        source: variant.source,
        scenarioDir: join(scenarioDir, "sweep", `${i + 1}`),
      });

      const results = await this.#executeRuns(
        variant.source,
        family,
        variantName,
        runsPerCombo,
        request.maxSteps,
      );
      const aggregate = aggregateSimulationRuns(results);
      runResults.push({
        variables,
        score: aggregate.score,
        reasoning: aggregate.reasoning,
        dimensionScores: aggregate.dimensionScores,
      });
    }

    return {
      dimensions,
      runs: runResults.length * runsPerCombo,
      results: runResults,
    };
  }

  async #executeSingle(
    source: string,
    family: ScenarioFamilyName,
    _name: string,
    seed: number,
    maxSteps?: number,
  ): Promise<{
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
  }> {
    const scenario = loadGeneratedSimulationScenario(source);
    return executeSimulationFamily(scenario, family, { seed, maxSteps });
  }

  #cartesianProduct(
    dimensions: SweepDimension[],
  ): Array<Record<string, unknown>> {
    if (dimensions.length === 0) return [{}];
    const [first, ...rest] = dimensions;
    const restCombos = this.#cartesianProduct(rest);
    const combos: Array<Record<string, unknown>> = [];
    for (const val of first.values) {
      for (const rest of restCombos) {
        combos.push({ [first.name]: val, ...rest });
      }
    }
    return combos;
  }
}
