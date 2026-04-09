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

import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import type { ScenarioFamilyName } from "../scenarios/families.js";
import { detectScenarioFamily } from "../scenarios/scenario-creator.js";
import { generateScenarioSource } from "../scenarios/codegen/registry.js";
import { validateGeneratedScenario } from "../scenarios/codegen/execution-validator.js";
import { healSpec } from "../scenarios/spec-auto-heal.js";
import {
  getScenarioTypeMarker,
  SIMULATION_LIKE_FAMILIES,
} from "../scenarios/families.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SimulationRequest {
  description: string;
  variables?: Record<string, unknown>;
  sweep?: SweepDimension[];
  runs?: number;
  maxSteps?: number;
  saveAs?: string;
}

// SweepDimension is now defined in sweep-dsl.ts (AC-454)
import type { SweepDimension } from "./sweep-dsl.js";
export type { SweepDimension } from "./sweep-dsl.js";

export interface SweepResult {
  dimensions: SweepDimension[];
  runs: number;
  results: Array<{
    variables: Record<string, unknown>;
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
  }>;
}

export interface SimulationSummary {
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
  bestCase?: { score: number; variables: Record<string, unknown> };
  worstCase?: { score: number; variables: Record<string, unknown> };
  mostSensitiveVariables?: string[];
}

export interface SimulationExecutionConfig {
  /**
   * Number of repeated runs for a single scenario variant, or per sweep cell
   * when `sweep` is present.
   */
  runs: number;
  maxSteps?: number;
  sweep?: SweepDimension[];
}

/** Simulation run status — completed, degraded (low fidelity), or failed (AC-532). */
export type SimulationStatus = "completed" | "degraded" | "failed";

/** Score below this threshold marks a run as degraded rather than completed (AC-532). */
export const DEGRADED_SCORE_THRESHOLD = 0.2;

/** Derive simulation status from score (AC-532). */
export function deriveSimulationStatus(score: number): SimulationStatus {
  return score >= DEGRADED_SCORE_THRESHOLD ? "completed" : "degraded";
}

export interface SimulationResult {
  id: string;
  name: string;
  family: ScenarioFamilyName;
  status: SimulationStatus;
  description: string;
  assumptions: string[];
  variables: Record<string, unknown>;
  sweep?: SweepResult;
  summary: SimulationSummary;
  execution?: SimulationExecutionConfig;
  artifacts: {
    scenarioDir: string;
    reportPath?: string;
  };
  warnings: string[];
  error?: string;
  /** Present on replay results — the id of the original simulation */
  replayOf?: string;
  /** Present on replay results — the original simulation's score */
  originalScore?: number;
  /** Present on replay results — delta between replay and original score */
  scoreDelta?: number;
}

export interface ReplayRequest {
  /** ID (name) of the saved simulation to replay */
  id: string;
  /** Optional variable overrides for the replay */
  variables?: Record<string, unknown>;
  /** Optional max steps override */
  maxSteps?: number;
}

export interface CompareRequest {
  left: string;
  right: string;
}

export interface VariableDelta {
  left: unknown;
  right: unknown;
  delta?: number;
}

export interface SimulationCompareResult {
  status: SimulationStatus;
  left: { name: string; score: number; variables: Record<string, unknown> };
  right: { name: string; score: number; variables: Record<string, unknown> };
  scoreDelta: number;
  variableDeltas: Record<string, VariableDelta>;
  dimensionDeltas: Record<
    string,
    { left: number; right: number; delta: number }
  >;
  likelyDrivers: string[];
  summary: string;
  reportPath?: string;
  error?: string;
}

interface BuiltSimulationVariant {
  spec: Record<string, unknown>;
  source: string;
}

interface ReplayVariant {
  spec: Record<string, unknown>;
  source: string;
  variables: Record<string, unknown>;
}

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
  private provider: LLMProvider;
  private knowledgeRoot: string;

  constructor(provider: LLMProvider, knowledgeRoot: string) {
    this.provider = provider;
    this.knowledgeRoot = knowledgeRoot;
  }

  /**
   * Run a simulation from a plain-language description.
   */
  async run(request: SimulationRequest): Promise<SimulationResult> {
    const id = generateId();
    const name = request.saveAs ?? this.deriveName(request.description);
    const family = this.inferFamily(request.description) as ScenarioFamilyName;
    const scenarioDir = join(this.knowledgeRoot, "_simulations", name);
    const execution = this.buildExecutionConfig(request);

    try {
      const baseVariant = await this.buildVariant(
        request.description,
        family,
        name,
        request.variables,
      );
      this.persistArtifacts(
        name,
        family,
        baseVariant.spec,
        baseVariant.source,
        scenarioDir,
      );

      // Execute — single or sweep
      let summary: SimulationSummary;
      let sweepResult: SweepResult | undefined;

      if (request.sweep && request.sweep.length > 0) {
        const sweepData = await this.executeSweep(
          request.description,
          family,
          name,
          request,
          scenarioDir,
        );
        sweepResult = sweepData;
        summary = this.aggregateSweep(sweepData);
      } else {
        const results = await this.executeRuns(
          baseVariant.source,
          family,
          name,
          execution.runs,
          execution.maxSteps,
        );
        summary = this.aggregateRuns(results);
      }

      const assumptions = this.buildAssumptions(
        baseVariant.spec,
        family,
        request.variables,
      );
      const warnings = this.buildWarnings(family, this.provider.name);

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
    const scenarioDir = join(this.knowledgeRoot, "_simulations", name);

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
    const execution = this.resolveExecutionConfig(originalReport);
    const replayMaxSteps = request.maxSteps ?? execution.maxSteps;

    try {
      let summary: SimulationSummary;
      let sweepResult: SweepResult | undefined;
      let variables: Record<string, unknown>;

      if (execution.sweep && execution.sweep.length > 0) {
        const replayedSweep = await this.replaySweep(
          scenarioDir,
          family,
          name,
          execution,
          originalReport,
          request.variables,
          replayMaxSteps,
        );
        sweepResult = replayedSweep;
        summary = this.aggregateSweep(replayedSweep);
        variables = this.collectReplayVariables(
          originalReport,
          request.variables,
        );
      } else {
        const variant = await this.loadReplayVariant(
          scenarioDir,
          family,
          name,
          this.collectReplayVariables(originalReport, request.variables),
          Object.keys(request.variables ?? {}).length > 0,
        );
        variables = variant.variables;
        const results = await this.executeRuns(
          variant.source,
          family,
          name,
          execution.runs,
          replayMaxSteps,
        );
        summary = this.aggregateRuns(results);
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
        scoreDelta: Math.round((summary.score - originalScore) * 10000) / 10000,
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
        variables: this.collectReplayVariables(
          originalReport,
          request.variables,
        ),
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
    const leftReport = this.loadReport(request.left);
    const rightReport = this.loadReport(request.right);

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
    const scoreDelta = Math.round((rightScore - leftScore) * 10000) / 10000;

    // Variable deltas
    const leftVars = this.collectCompareVariables(leftReport);
    const rightVars = this.collectCompareVariables(rightReport);
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
          ? Math.round((rv - lv) * 10000) / 10000
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
        left: lv,
        right: rv,
        delta: Math.round((rv - lv) * 10000) / 10000,
      };
    }

    // Likely drivers: variables that changed AND where dimensions shifted
    const likelyDrivers: string[] = [];
    for (const [key, vd] of Object.entries(variableDeltas)) {
      if (!this.valuesEqual(vd.left, vd.right)) {
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
    const reportsDir = join(this.knowledgeRoot, "_simulations", "_comparisons");
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

  private loadReport(name: string): SimulationResult | null {
    const simulationsRoot = join(this.knowledgeRoot, "_simulations");
    const baseReportPath = join(simulationsRoot, name, "report.json");
    if (existsSync(baseReportPath)) {
      try {
        return JSON.parse(
          readFileSync(baseReportPath, "utf-8"),
        ) as SimulationResult;
      } catch {
        return null;
      }
    }

    if (!existsSync(simulationsRoot)) return null;

    for (const entry of readdirSync(simulationsRoot, { withFileTypes: true })) {
      if (!entry.isDirectory() || entry.name.startsWith("_")) continue;
      const replayReportPath = join(
        simulationsRoot,
        entry.name,
        `replay_${name}.json`,
      );
      if (!existsSync(replayReportPath)) continue;
      try {
        return JSON.parse(
          readFileSync(replayReportPath, "utf-8"),
        ) as SimulationResult;
      } catch {
        return null;
      }
    }

    return null;
  }

  private collectCompareVariables(
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
        if (!existing.some((entry) => this.valuesEqual(entry, value))) {
          existing.push(value);
          valueSets.set(key, existing);
        }
      }
    }

    for (const [key, values] of valueSets.entries()) {
      if (
        key in merged &&
        values.length === 1 &&
        this.valuesEqual(merged[key], values[0])
      ) {
        continue;
      }
      merged[key] = values.length === 1 ? values[0] : values;
    }

    return merged;
  }

  private valuesEqual(left: unknown, right: unknown): boolean {
    return JSON.stringify(left) === JSON.stringify(right);
  }

  // Internals
  // -------------------------------------------------------------------------

  private failedResult(
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

  private inferFamily(description: string): string {
    const family = detectScenarioFamily(description);
    if (SIMULATION_LIKE_FAMILIES.has(family)) return family;
    return "simulation";
  }

  private buildExecutionConfig(
    request: SimulationRequest,
  ): SimulationExecutionConfig {
    return {
      runs: Math.max(1, request.runs ?? 1),
      maxSteps: request.maxSteps,
      sweep:
        request.sweep && request.sweep.length > 0 ? request.sweep : undefined,
    };
  }

  private resolveExecutionConfig(
    report: SimulationResult,
  ): SimulationExecutionConfig {
    if (report.execution) {
      return {
        runs: Math.max(1, report.execution.runs ?? 1),
        maxSteps: report.execution.maxSteps,
        sweep:
          report.execution.sweep && report.execution.sweep.length > 0
            ? report.execution.sweep
            : undefined,
      };
    }

    if (report.sweep && report.sweep.results.length > 0) {
      const runsPerCell = Math.max(
        1,
        Math.round(
          report.sweep.runs / Math.max(report.sweep.results.length, 1),
        ),
      );
      return {
        runs: runsPerCell,
        sweep: report.sweep.dimensions,
      };
    }

    return { runs: 1 };
  }

  private collectReplayVariables(
    originalReport: SimulationResult,
    overrides?: Record<string, unknown>,
  ): Record<string, unknown> {
    return {
      ...(originalReport.variables ?? {}),
      ...(overrides ?? {}),
    };
  }

  private loadPersistedSpec(specPath: string): Record<string, unknown> | null {
    if (!existsSync(specPath)) return null;
    const persisted = JSON.parse(readFileSync(specPath, "utf-8")) as Record<
      string,
      unknown
    >;
    const { name: _name, family: _family, ...spec } = persisted;
    return spec;
  }

  private async loadReplayVariant(
    scenarioDir: string,
    family: ScenarioFamilyName,
    name: string,
    variables: Record<string, unknown>,
    regenerate: boolean,
  ): Promise<ReplayVariant> {
    const sourcePath = join(scenarioDir, "scenario.js");
    const specPath = join(scenarioDir, "spec.json");
    const savedSpec = this.loadPersistedSpec(specPath);

    if (!regenerate && existsSync(sourcePath)) {
      return {
        spec: savedSpec ?? {},
        source: readFileSync(sourcePath, "utf-8"),
        variables,
      };
    }

    if (!savedSpec) {
      throw new Error(`Saved simulation spec not found at ${specPath}`);
    }

    const spec = this.applyVariableOverrides(savedSpec, family, variables);
    const validation = await validateGeneratedScenario(
      generateScenarioSource(family, spec, name),
      family,
      name,
    );
    if (!validation.valid) {
      throw new Error(validation.errors.join("; "));
    }

    return {
      spec,
      source: generateScenarioSource(family, spec, name),
      variables,
    };
  }

  private async replaySweep(
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
      const variant = await this.loadReplayVariant(
        variantDir,
        family,
        variantName,
        variables,
        regenerate,
      );
      const rerunResults = await this.executeRuns(
        variant.source,
        family,
        variantName,
        execution.runs,
        maxSteps,
      );
      const aggregate = this.aggregateRuns(rerunResults);
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

  private async buildSpec(
    description: string,
    family: string,
    variables?: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const serializedVariables =
      variables && Object.keys(variables).length > 0
        ? JSON.stringify(variables, null, 2)
        : "";
    const systemPrompt = `You are a simulation designer. Given a plain-language description, produce a ${family} spec as a JSON object.

Required fields:
- description: scenario summary
- environment_description: system context
- initial_state_description: starting state
- success_criteria: array of strings
- failure_modes: array of strings
- max_steps: positive integer
- actions: array of {name, description, parameters, preconditions, effects}
${family === "operator_loop" ? "- escalation_policy: {escalation_threshold, max_escalations}" : ""}
${family === "coordination" ? "- workers: array of {worker_id, role} with at least 2 workers" : ""}

${
  serializedVariables
    ? `Incorporate these requested simulation parameters directly into the returned spec so they materially change execution when they change:
${serializedVariables}

Prefer mapping them into native fields like max_steps, escalation_policy, workers, action parameters, environment details, or other family-appropriate controls. If a parameter does not cleanly fit a native field, preserve it under simulation_variables.`
    : ""
}

Output ONLY the JSON object, no markdown fences.`;

    const result = await this.provider.complete({
      systemPrompt,
      userPrompt: `Simulation request: ${description}${serializedVariables ? `\n\nRequested parameters:\n${serializedVariables}` : ""}`,
    });

    const parsed = this.parseJSON(result.text);
    if (!parsed) {
      throw new Error("Simulation spec generation did not return valid JSON");
    }
    return parsed;
  }

  private async buildVariant(
    description: string,
    family: ScenarioFamilyName,
    name: string,
    variables?: Record<string, unknown>,
  ): Promise<BuiltSimulationVariant> {
    const rawSpec = await this.buildSpec(description, family, variables);
    const healedSpec = this.applyVariableOverrides(
      healSpec(rawSpec, family),
      family,
      variables,
    );
    const source = generateScenarioSource(family, healedSpec, name);
    const validation = await validateGeneratedScenario(source, family, name);
    if (!validation.valid) {
      throw new Error(validation.errors.join("; "));
    }
    return { spec: healedSpec, source };
  }

  private applyVariableOverrides(
    spec: Record<string, unknown>,
    family: ScenarioFamilyName,
    variables?: Record<string, unknown>,
  ): Record<string, unknown> {
    if (!variables || Object.keys(variables).length === 0) {
      return spec;
    }

    const next: Record<string, unknown> = { ...spec };
    const passthrough: Record<string, unknown> = {};

    for (const [key, value] of Object.entries(variables)) {
      switch (key) {
        case "max_steps":
        case "maxSteps": {
          const maxSteps = Number(value);
          if (Number.isFinite(maxSteps) && maxSteps > 0) {
            next.max_steps = Math.floor(maxSteps);
          }
          break;
        }
        case "escalation_threshold":
        case "escalationThreshold": {
          if (family === "operator_loop") {
            const policy = {
              ...((next.escalation_policy as Record<string, unknown>) ?? {}),
            };
            policy.escalation_threshold = value;
            next.escalation_policy = policy;
          } else {
            passthrough[key] = value;
          }
          break;
        }
        case "max_escalations":
        case "maxEscalations": {
          const maxEscalations = Number(value);
          if (
            family === "operator_loop" &&
            Number.isFinite(maxEscalations) &&
            maxEscalations > 0
          ) {
            const policy = {
              ...((next.escalation_policy as Record<string, unknown>) ?? {}),
            };
            policy.max_escalations = Math.floor(maxEscalations);
            next.escalation_policy = policy;
          } else {
            passthrough[key] = value;
          }
          break;
        }
        case "worker_count":
        case "workerCount": {
          const workerCount = Number(value);
          if (
            family === "coordination" &&
            Number.isFinite(workerCount) &&
            workerCount >= 2
          ) {
            const existingWorkers = Array.isArray(next.workers)
              ? [...(next.workers as Array<Record<string, unknown>>)]
              : [];
            const normalizedCount = Math.floor(workerCount);
            const workers = existingWorkers.slice(0, normalizedCount);
            while (workers.length < normalizedCount) {
              workers.push({
                worker_id: `worker_${workers.length + 1}`,
                role: `Worker ${workers.length + 1}`,
              });
            }
            next.workers = workers;
          } else {
            passthrough[key] = value;
          }
          break;
        }
        default:
          passthrough[key] = value;
      }
    }

    if (Object.keys(passthrough).length > 0) {
      const existingVariables =
        next.simulation_variables &&
        typeof next.simulation_variables === "object"
          ? (next.simulation_variables as Record<string, unknown>)
          : {};
      next.simulation_variables = { ...existingVariables, ...passthrough };
    }

    return next;
  }

  private async executeRuns(
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
      const result = await this.executeSingle(
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

  private async executeSweep(
    description: string,
    family: ScenarioFamilyName,
    name: string,
    request: SimulationRequest,
    scenarioDir: string,
  ): Promise<SweepResult> {
    const dimensions = request.sweep ?? [];
    const runResults: SweepResult["results"] = [];
    const runsPerCombo = Math.max(1, request.runs ?? 1);

    const combos = this.cartesianProduct(dimensions);
    for (let i = 0; i < combos.length; i++) {
      const variables = { ...(request.variables ?? {}), ...combos[i] };
      const variantName = `${name}__sweep_${i + 1}`;
      const variant = await this.buildVariant(
        description,
        family,
        variantName,
        variables,
      );
      this.persistArtifacts(
        variantName,
        family,
        variant.spec,
        variant.source,
        join(scenarioDir, "sweep", `${i + 1}`),
      );

      const results = await this.executeRuns(
        variant.source,
        family,
        variantName,
        runsPerCombo,
        request.maxSteps,
      );
      const aggregate = this.aggregateRuns(results);
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

  private async executeSingle(
    source: string,
    family: ScenarioFamilyName,
    name: string,
    seed: number,
    maxSteps?: number,
  ): Promise<{
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
  }> {
    const moduleObj = { exports: {} as Record<string, unknown> };
    const fn = new Function("module", "exports", source);
    fn(moduleObj, moduleObj.exports);
    const scenario = (
      moduleObj.exports as {
        scenario: Record<string, (...args: unknown[]) => unknown>;
      }
    ).scenario;

    switch (family) {
      case "operator_loop":
        return this.executeOperatorLoopScenario(scenario, seed, maxSteps);
      case "coordination":
        return this.executeCoordinationScenario(scenario, seed, maxSteps);
      default:
        return this.executeGenericScenario(scenario, seed, maxSteps);
    }
  }

  private executeGenericScenario(
    scenario: Record<string, (...args: unknown[]) => unknown>,
    seed: number,
    maxSteps?: number,
  ): {
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
  } {
    let state = scenario.initialState(seed) as Record<string, unknown>;
    const limit = maxSteps ?? 20;
    let steps = 0;
    const records: Array<{ result: { success: boolean } }> = [];

    while (steps < limit) {
      const terminal = scenario.isTerminal(state) as boolean;
      if (terminal) break;
      const actions = scenario.getAvailableActions(state) as Array<{
        name: string;
      }>;
      if (!actions || actions.length === 0) break;
      const actionResult = scenario.executeAction(state, {
        name: actions[0].name,
        parameters: {},
      }) as {
        result: Record<string, unknown>;
        state: Record<string, unknown>;
      };
      records.push({ result: { success: !!actionResult.result?.success } });
      state = actionResult.state;
      steps++;
    }

    const evalResult = scenario.getResult(state, { records }) as {
      score: number;
      reasoning: string;
      dimensionScores?: Record<string, number>;
    };

    return {
      score: evalResult.score ?? 0,
      reasoning: evalResult.reasoning ?? "",
      dimensionScores: evalResult.dimensionScores ?? {},
    };
  }

  private executeOperatorLoopScenario(
    scenario: Record<string, (...args: unknown[]) => unknown>,
    seed: number,
    maxSteps?: number,
  ): {
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
  } {
    let state = scenario.initialState(seed) as Record<string, unknown>;
    const limit = maxSteps ?? 20;
    let steps = 0;
    let requestedClarification = false;
    let escalated = false;
    const records: Array<{ result: { success: boolean } }> = [];

    while (steps < limit) {
      const terminal = scenario.isTerminal(state) as boolean;
      if (terminal) break;

      if (
        !requestedClarification &&
        typeof scenario.requestClarification === "function"
      ) {
        state = scenario.requestClarification(state, {
          question: "Clarify the current uncertainty before continuing.",
          urgency: "medium",
        }) as Record<string, unknown>;
        requestedClarification = true;
      }

      const actions = scenario.getAvailableActions(state) as Array<{
        name: string;
        parameters?: Record<string, unknown>;
      }>;
      if (!actions || actions.length === 0) break;

      const action = {
        name: String(actions[0]?.name ?? "unknown"),
        parameters:
          actions[0]?.parameters && typeof actions[0].parameters === "object"
            ? actions[0].parameters
            : {},
      };
      const actionResult = scenario.executeAction(state, action) as {
        result: Record<string, unknown>;
        state: Record<string, unknown>;
      };
      records.push({ result: { success: !!actionResult.result?.success } });
      state = actionResult.state ?? state;

      const situations = Array.isArray(state.situationsRequiringEscalation)
        ? (state.situationsRequiringEscalation as Array<
            Record<string, unknown>
          >)
        : [];
      const latest = situations[situations.length - 1];
      if (latest && typeof scenario.escalate === "function") {
        state = scenario.escalate(state, {
          reason: String(latest.reason ?? "action failure"),
          severity: String(latest.severity ?? "high"),
          wasNecessary: true,
        }) as Record<string, unknown>;
        escalated = true;
      }
      steps++;
    }

    if (!escalated && typeof scenario.escalate === "function") {
      state = scenario.escalate(state, {
        reason: "Mandatory operator review checkpoint.",
        severity: "low",
        wasNecessary: true,
      }) as Record<string, unknown>;
    }

    const evalResult = scenario.getResult(state, { records }) as {
      score: number;
      reasoning: string;
      dimensionScores?: Record<string, number>;
    };

    return {
      score: evalResult.score ?? 0,
      reasoning: evalResult.reasoning ?? "",
      dimensionScores: evalResult.dimensionScores ?? {},
    };
  }

  private executeCoordinationScenario(
    scenario: Record<string, (...args: unknown[]) => unknown>,
    seed: number,
    maxSteps?: number,
  ): {
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
  } {
    let state = scenario.initialState(seed) as Record<string, unknown>;
    const limit = maxSteps ?? 20;
    let steps = 0;
    let workerIndex = 0;
    const records: Array<{ result: { success: boolean } }> = [];
    const workerContexts =
      typeof scenario.getWorkerContexts === "function"
        ? (scenario.getWorkerContexts() as Array<Record<string, unknown>>)
        : [];
    const workerIds = workerContexts.map((worker, index) =>
      String(worker.workerId ?? worker.id ?? `worker_${index + 1}`),
    );

    while (steps < limit) {
      const terminal = scenario.isTerminal(state) as boolean;
      if (terminal) break;

      const actions = scenario.getAvailableActions(state) as Array<{
        name: string;
        parameters?: Record<string, unknown>;
      }>;
      if (!actions || actions.length === 0) break;

      const action = {
        name: String(actions[0]?.name ?? "unknown"),
        parameters:
          actions[0]?.parameters && typeof actions[0].parameters === "object"
            ? actions[0].parameters
            : {},
      };

      if (
        workerIds.length > 1 &&
        typeof scenario.recordHandoff === "function"
      ) {
        const fromWorker = workerIds[workerIndex % workerIds.length];
        const toWorker = workerIds[(workerIndex + 1) % workerIds.length];
        state = scenario.recordHandoff(state, fromWorker, toWorker, {
          action: action.name,
          step: steps + 1,
        }) as Record<string, unknown>;
      }

      const actionResult = scenario.executeAction(state, action) as {
        result: Record<string, unknown>;
        state: Record<string, unknown>;
      };
      records.push({ result: { success: !!actionResult.result?.success } });
      state = actionResult.state ?? state;

      if (workerIds.length > 0 && typeof scenario.mergeOutputs === "function") {
        const workerId = workerIds[workerIndex % workerIds.length];
        state = scenario.mergeOutputs(state, {
          [workerId]: [String(actionResult.result?.output ?? action.name)],
        }) as Record<string, unknown>;
      }

      workerIndex++;
      steps++;
    }

    const evalResult = scenario.getResult(state, { records }) as {
      score: number;
      reasoning: string;
      dimensionScores?: Record<string, number>;
    };

    return {
      score: evalResult.score ?? 0,
      reasoning: evalResult.reasoning ?? "",
      dimensionScores: evalResult.dimensionScores ?? {},
    };
  }

  private aggregateRuns(
    results: Array<{
      score: number;
      reasoning: string;
      dimensionScores: Record<string, number>;
    }>,
  ): SimulationSummary {
    if (results.length === 0)
      return { score: 0, reasoning: "No runs completed", dimensionScores: {} };
    if (results.length === 1) return results[0];

    const avgScore =
      results.reduce((sum, r) => sum + r.score, 0) / results.length;
    const best = results.reduce((a, b) => (a.score > b.score ? a : b));
    const worst = results.reduce((a, b) => (a.score < b.score ? a : b));

    return {
      score: Math.round(avgScore * 10000) / 10000,
      reasoning: `Average across ${results.length} runs`,
      dimensionScores: results[0].dimensionScores,
      bestCase: { score: best.score, variables: {} },
      worstCase: { score: worst.score, variables: {} },
    };
  }

  private aggregateSweep(sweep: SweepResult): SimulationSummary {
    const results = sweep.results;
    if (results.length === 0)
      return {
        score: 0,
        reasoning: "No sweep runs completed",
        dimensionScores: {},
      };

    const avgScore =
      results.reduce((sum, r) => sum + r.score, 0) / results.length;
    const best = results.reduce((a, b) => (a.score > b.score ? a : b));
    const worst = results.reduce((a, b) => (a.score < b.score ? a : b));

    // Sensitivity analysis: for each dimension, how much does score vary?
    const sensitivity: Array<{ name: string; variance: number }> = [];
    for (const dim of sweep.dimensions) {
      const scoresByValue = new Map<number, number[]>();
      for (const r of results) {
        const val = r.variables[dim.name] as number;
        if (val != null) {
          const scores = scoresByValue.get(val) ?? [];
          scores.push(r.score);
          scoresByValue.set(val, scores);
        }
      }
      const means = [...scoresByValue.values()].map(
        (s) => s.reduce((a, b) => a + b, 0) / s.length,
      );
      if (means.length > 1) {
        const range = Math.max(...means) - Math.min(...means);
        sensitivity.push({ name: dim.name, variance: range });
      }
    }
    sensitivity.sort((a, b) => b.variance - a.variance);

    return {
      score: Math.round(avgScore * 10000) / 10000,
      reasoning: `Sweep across ${sweep.dimensions.length} dimension(s), ${results.length} runs`,
      dimensionScores: results[0].dimensionScores,
      bestCase: { score: best.score, variables: best.variables },
      worstCase: { score: worst.score, variables: worst.variables },
      mostSensitiveVariables: sensitivity.map((s) => s.name),
    };
  }

  private buildAssumptions(
    spec: Record<string, unknown>,
    family: string,
    variables?: Record<string, unknown>,
  ): string[] {
    const assumptions: string[] = [];
    assumptions.push(
      `Modeled as a ${family} scenario with ${(spec.actions as unknown[])?.length ?? 0} actions`,
    );
    if (spec.max_steps || spec.maxSteps) {
      assumptions.push(
        `Bounded to ${spec.max_steps ?? spec.maxSteps} maximum steps`,
      );
    }
    if (spec.success_criteria || spec.successCriteria) {
      const criteria = (spec.success_criteria ??
        spec.successCriteria) as string[];
      assumptions.push(`Success defined as: ${criteria.join(", ")}`);
    }
    if (variables && Object.keys(variables).length > 0) {
      assumptions.push(`Requested parameters: ${JSON.stringify(variables)}`);
    }
    if (family === "operator_loop") {
      assumptions.push(
        "Runtime includes at least one clarification request and an operator review checkpoint.",
      );
    }
    if (family === "coordination") {
      assumptions.push(
        "Runtime records worker handoffs and merges outputs during execution.",
      );
    }
    assumptions.push("Agent selects actions greedily (first available)");
    assumptions.push(
      "Environment is deterministic given the same seed and parameter set",
    );
    return assumptions;
  }

  private buildWarnings(family: string, providerName: string): string[] {
    const warnings = [
      "Model-driven result only; not empirical evidence.",
      `Simulated using the ${family} family with generated action logic.`,
      "Outcomes depend on the quality of the LLM-generated scenario spec.",
      "Variable sensitivity analysis is based on score variance across sweep values, not causal attribution.",
    ];
    if (providerName === "deterministic") {
      warnings.push(
        "Synthetic deterministic provider in use; results are placeholder and not model-derived.",
      );
    }
    return warnings;
  }

  private persistArtifacts(
    name: string,
    family: string,
    spec: Record<string, unknown>,
    source: string,
    scenarioDir = join(this.knowledgeRoot, "_simulations", name),
  ): string {
    if (!existsSync(scenarioDir)) mkdirSync(scenarioDir, { recursive: true });

    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify({ name, family, ...spec }, null, 2),
      "utf-8",
    );
    writeFileSync(join(scenarioDir, "scenario.js"), source, "utf-8");
    writeFileSync(
      join(scenarioDir, "scenario_type.txt"),
      getScenarioTypeMarker(family as ScenarioFamilyName),
      "utf-8",
    );

    return scenarioDir;
  }

  private deriveName(description: string): string {
    return (
      description
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, "")
        .split(/\s+/)
        .filter((w) => w.length > 2)
        .slice(0, 4)
        .join("_") || "simulation"
    );
  }

  private parseJSON(text: string): Record<string, unknown> | null {
    const trimmed = text.trim();
    try {
      return JSON.parse(trimmed);
    } catch {
      /* continue */
    }
    const start = trimmed.indexOf("{");
    const end = trimmed.lastIndexOf("}");
    if (start !== -1 && end > start) {
      try {
        return JSON.parse(trimmed.slice(start, end + 1));
      } catch {
        /* continue */
      }
    }
    return null;
  }

  private cartesianProduct(
    dimensions: SweepDimension[],
  ): Array<Record<string, unknown>> {
    if (dimensions.length === 0) return [{}];
    const [first, ...rest] = dimensions;
    const restCombos = this.cartesianProduct(rest);
    const combos: Array<Record<string, unknown>> = [];
    for (const val of first.values) {
      for (const rest of restCombos) {
        combos.push({ [first.name]: val, ...rest });
      }
    }
    return combos;
  }
}
