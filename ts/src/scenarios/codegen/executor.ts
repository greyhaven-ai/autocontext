/**
 * Generated scenario executor — runs persisted/generated scenarios through
 * ScenarioRuntime and returns a deterministic summary.
 */

import { join } from "node:path";
import type { ScenarioFamilyName } from "../families.js";
import { loadCustomScenario } from "./loader.js";
import { CodegenUnsupportedFamilyError, ScenarioRuntime, type ScenarioProxy } from "./runtime.js";

export interface GeneratedScenarioActionRecord {
  action: { name: string; parameters: Record<string, unknown> };
  result: Record<string, unknown>;
}

export interface GeneratedScenarioExecutionResult {
  family: ScenarioFamilyName;
  stepsExecuted: number;
  finalState: Record<string, unknown>;
  records: GeneratedScenarioActionRecord[];
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
}

async function resolveMaxSteps(proxy: ScenarioProxy, fallback = 20): Promise<number> {
  try {
    const value = await proxy.call<number>("maxSteps");
    if (typeof value === "number" && Number.isFinite(value) && value > 0) {
      return Math.floor(value);
    }
  } catch {
    // Families without maxSteps() fall back to the requested/default limit.
  }
  return fallback;
}

async function executeActionScenario(
  proxy: ScenarioProxy,
  family: ScenarioFamilyName,
  opts: { seed?: number; maxSteps?: number },
): Promise<GeneratedScenarioExecutionResult> {
  let state = await proxy.call<Record<string, unknown>>("initialState", opts.seed ?? 42);
  const records: GeneratedScenarioActionRecord[] = [];
  const maxSteps = await resolveMaxSteps(proxy, opts.maxSteps ?? 20);

  while (records.length < maxSteps) {
    const terminal = await proxy.call<boolean>("isTerminal", state);
    if (terminal) break;

    const actions = await proxy.call<Array<{ name: string; parameters?: Record<string, unknown> }>>(
      "getAvailableActions",
      state,
    );
    if (!actions || actions.length === 0) break;

    const action = {
      name: String(actions[0]?.name ?? "unknown"),
      parameters:
        actions[0]?.parameters && typeof actions[0].parameters === "object"
          ? actions[0].parameters
          : {},
    };
    const actionResult = await proxy.call<{
      result: Record<string, unknown>;
      state: Record<string, unknown>;
    }>("executeAction", state, action);
    records.push({
      action,
      result: actionResult.result ?? {},
    });
    state = actionResult.state ?? state;
  }

  const result = await proxy.call<{
    score: number;
    reasoning: string;
    dimensionScores?: Record<string, number>;
  }>("getResult", state, { records });

  return {
    family,
    stepsExecuted: records.length,
    finalState: state,
    records,
    score: result.score,
    reasoning: result.reasoning,
    dimensionScores: result.dimensionScores ?? {},
  };
}

async function executeArtifactEditingScenario(
  proxy: ScenarioProxy,
  opts: { seed?: number },
): Promise<GeneratedScenarioExecutionResult> {
  const state = await proxy.call<Record<string, unknown>>("initialState", opts.seed ?? 42);
  const artifacts = await proxy.call<Array<Record<string, unknown>>>("initialArtifacts");
  const prompt = await proxy.call<string>("getEditPrompt", artifacts, state);
  const result = await proxy.call<{
    score: number;
    reasoning: string;
    dimensionScores?: Record<string, number>;
  }>("evaluateOutput", artifacts, state);

  return {
    family: "artifact_editing",
    stepsExecuted: 1,
    finalState: { ...state, artifacts },
    records: [{
      action: { name: "evaluate_artifacts", parameters: {} },
      result: {
        prompt,
        artifactCount: artifacts.length,
        score: result.score,
      },
    }],
    score: result.score,
    reasoning: result.reasoning,
    dimensionScores: result.dimensionScores ?? {},
  };
}

async function executeGeneratedScenarioProxy(
  proxy: ScenarioProxy,
  family: ScenarioFamilyName,
  opts: { seed?: number; maxSteps?: number },
): Promise<GeneratedScenarioExecutionResult> {
  switch (family) {
    case "artifact_editing":
      return executeArtifactEditingScenario(proxy, opts);
    case "simulation":
    case "investigation":
    case "workflow":
    case "negotiation":
    case "schema_evolution":
    case "tool_fragility":
    case "coordination":
      return executeActionScenario(proxy, family, opts);
    case "agent_task":
    case "game":
    case "operator_loop":
      throw new CodegenUnsupportedFamilyError(family);
  }
}

export async function executeGeneratedScenarioSource(opts: {
  source: string;
  family: ScenarioFamilyName;
  name: string;
  seed?: number;
  maxSteps?: number;
}): Promise<GeneratedScenarioExecutionResult> {
  const runtime = new ScenarioRuntime();
  try {
    const proxy = await runtime.loadScenario(opts.source, opts.family, opts.name);
    return await executeGeneratedScenarioProxy(proxy, opts.family, opts);
  } finally {
    runtime.dispose();
  }
}

export async function executeGeneratedScenarioEntry(opts: {
  customDir: string;
  name: string;
  family: ScenarioFamilyName;
  seed?: number;
  maxSteps?: number;
}): Promise<GeneratedScenarioExecutionResult> {
  const proxy = await loadCustomScenario(
    opts.customDir,
    opts.name,
    opts.family,
  );
  try {
    return await executeGeneratedScenarioProxy(proxy, opts.family, opts);
  } finally {
    proxy.dispose();
  }
}

export function customScenarioDirectory(knowledgeRoot: string): string {
  return join(knowledgeRoot, "_custom_scenarios");
}
