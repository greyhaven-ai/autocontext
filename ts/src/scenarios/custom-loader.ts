/**
 * Custom scenario loader — scan knowledge dir, load specs, register (AC-348 Task 29).
 * Mirrors Python's autocontext/scenarios/custom/registry.py.
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import type { ScenarioFamilyName } from "./families.js";
import type { AgentTaskInterface, LLMProvider } from "../types/index.js";
import { createAgentTask } from "./agent-task-factory.js";
import { parseRawSpec, type AgentTaskSpec } from "./agent-task-spec.js";
import { hasCodegen } from "./codegen/index.js";
import { readScenarioFamily } from "./codegen/loader.js";
import { customScenarioDirectory } from "./codegen/executor.js";

export interface CustomScenarioEntry {
  name: string;
  type: string;
  spec: Record<string, unknown>;
  path: string;
  /** Whether the scenario has a generated .js source that can be executed via ScenarioRuntime */
  hasGeneratedSource?: boolean;
}

export interface ResolvedCustomAgentTask {
  name: string;
  path: string;
  spec: AgentTaskSpec;
}

export const CUSTOM_SCENARIO_REGISTRY = new Map<string, CustomScenarioEntry>();
export const CUSTOM_AGENT_TASK_REGISTRY: Record<string, () => AgentTaskInterface> = {};

function normalizeAgentTaskSpec(spec: Record<string, unknown>): AgentTaskSpec {
  if ("taskPrompt" in spec && "judgeRubric" in spec) {
    return {
      taskPrompt: String(spec.taskPrompt ?? ""),
      judgeRubric: String(spec.judgeRubric ?? ""),
      outputFormat: String(spec.outputFormat ?? "free_text") as AgentTaskSpec["outputFormat"],
      judgeModel: String(spec.judgeModel ?? ""),
      difficultyTiers: (spec.difficultyTiers as AgentTaskSpec["difficultyTiers"]) ?? undefined,
      referenceContext: (spec.referenceContext as string | null | undefined) ?? undefined,
      referenceSources: (spec.referenceSources as string[] | null | undefined) ?? undefined,
      requiredConcepts: (spec.requiredConcepts as string[] | null | undefined) ?? undefined,
      calibrationExamples:
        (spec.calibrationExamples as Array<Record<string, unknown>> | null | undefined) ??
        undefined,
      contextPreparation: (spec.contextPreparation as string | null | undefined) ?? undefined,
      requiredContextKeys: (spec.requiredContextKeys as string[] | null | undefined) ?? undefined,
      maxRounds: Number(spec.maxRounds ?? 1),
      qualityThreshold: Number(spec.qualityThreshold ?? 0.9),
      revisionPrompt: (spec.revisionPrompt as string | null | undefined) ?? undefined,
      sampleInput: (spec.sampleInput as string | null | undefined) ?? undefined,
    };
  }
  if ("taskPrompt" in spec && "rubric" in spec) {
    return {
      taskPrompt: String(spec.taskPrompt ?? ""),
      judgeRubric: String(spec.rubric ?? ""),
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };
  }
  return parseRawSpec(spec);
}

export function renderAgentTaskPrompt(spec: AgentTaskSpec): string {
  let prompt = spec.taskPrompt;
  if (spec.sampleInput) {
    prompt += `\n\n## Input Data\n${spec.sampleInput}`;
  }
  return prompt;
}

function inferScenarioTypeFromSpec(spec: Record<string, unknown>): string {
  const declaredType = spec.scenario_type ?? spec.scenarioType;
  if (typeof declaredType === "string" && declaredType.trim().length > 0) {
    return declaredType.trim();
  }

  const hasParametricShape =
    Array.isArray(spec.strategy_params ?? spec.strategyParams) ||
    Array.isArray(spec.environment_variables ?? spec.environmentVariables) ||
    Array.isArray(spec.scoring_components ?? spec.scoringComponents);
  if (hasParametricShape) {
    return "parametric";
  }

  return "agent_task";
}

function readPersistedScenarioType(entryPath: string): string {
  const typePath = join(entryPath, "scenario_type.txt");
  if (existsSync(typePath)) {
    try {
      const stored = readFileSync(typePath, "utf-8").trim();
      if (stored.length > 0) {
        return stored;
      }
    } catch {
      return "agent_task";
    }
  }

  const candidateSpecPaths = [
    join(entryPath, "spec.json"),
    join(entryPath, "agent_task_spec.json"),
  ];
  for (const specPath of candidateSpecPaths) {
    if (!existsSync(specPath)) {
      continue;
    }
    try {
      const raw = JSON.parse(readFileSync(specPath, "utf-8")) as Record<string, unknown>;
      return inferScenarioTypeFromSpec(raw);
    } catch {
      continue;
    }
  }

  return "agent_task";
}

function scenarioTypeToFamily(type: string): ScenarioFamilyName | null {
  const TYPE_TO_FAMILY: Record<string, ScenarioFamilyName> = {
    parametric: "game",
    agent_task: "agent_task",
    simulation: "simulation",
    artifact_editing: "artifact_editing",
    investigation: "investigation",
    workflow: "workflow",
    schema_evolution: "schema_evolution",
    tool_fragility: "tool_fragility",
    negotiation: "negotiation",
    operator_loop: "operator_loop",
    coordination: "coordination",
  };
  return TYPE_TO_FAMILY[type] ?? null;
}

/**
 * Scan a custom scenarios directory and load spec.json entries.
 * Returns a Map of name → entry for each valid custom scenario found.
 */
export function loadCustomScenarios(customDir: string): Map<string, CustomScenarioEntry> {
  const loaded = new Map<string, CustomScenarioEntry>();

  if (!existsSync(customDir)) return loaded;

  let entries: string[];
  try {
    entries = readdirSync(customDir).sort();
  } catch {
    return loaded;
  }

  for (const name of entries) {
    const entryPath = join(customDir, name);
    try {
      if (!statSync(entryPath).isDirectory()) continue;
    } catch {
      continue;
    }

    const scenarioType = readPersistedScenarioType(entryPath);
    const specPath = join(entryPath, "spec.json");
    const agentTaskSpecPath = join(entryPath, "agent_task_spec.json");
    if (
      !existsSync(specPath) &&
      !(scenarioType === "agent_task" && existsSync(agentTaskSpecPath))
    ) {
      continue;
    }

    // Read spec
    try {
      const specSourcePath =
        scenarioType === "agent_task" && existsSync(agentTaskSpecPath)
          ? agentTaskSpecPath
          : specPath;
      const specRaw = readFileSync(specSourcePath, "utf-8");
      const rawSpec = JSON.parse(specRaw) as Record<string, unknown>;
      const spec = scenarioType === "agent_task" ? normalizeAgentTaskSpec(rawSpec) : rawSpec;
      const hasGenSource = existsSync(join(entryPath, "scenario.js"));
      const family = readScenarioFamily(entryPath) ?? scenarioTypeToFamily(scenarioType);
      loaded.set(name, {
        name,
        type: scenarioType,
        spec,
        path: entryPath,
        hasGeneratedSource: hasGenSource && family != null && hasCodegen(family),
      });
    } catch {
      // Skip malformed specs
      continue;
    }
  }

  return loaded;
}

/**
 * Register loaded custom scenarios into the custom scenario registries.
 * Agent-task scenarios are tracked separately from the game-scenario registry because
 * they do not satisfy the ScenarioInterface contract used by the generation loop.
 */
/**
 * Convenience: scan knowledge/_custom_scenarios/ and register everything.
 * Returns the number of custom scenarios discovered.
 * This mirrors Python's _load_persisted_custom_scenarios() at import time.
 */
function resolveCustomScenarioEntry(
  knowledgeRoot: string,
  name: string,
): CustomScenarioEntry | null {
  return loadCustomScenarios(customScenarioDirectory(knowledgeRoot)).get(name) ?? null;
}

export function discoverAndRegisterCustomScenarios(
  knowledgeRoot: string,
  provider?: LLMProvider,
): number {
  const loaded = loadCustomScenarios(customScenarioDirectory(knowledgeRoot));
  registerCustomScenarios(loaded, provider);
  return loaded.size;
}

export function resolveCustomAgentTask(
  knowledgeRoot: string,
  name: string,
): ResolvedCustomAgentTask | null {
  const entry = resolveCustomScenarioEntry(knowledgeRoot, name);
  if (!entry || entry.type !== "agent_task") {
    return null;
  }
  return {
    name,
    path: entry.path,
    spec: normalizeAgentTaskSpec(entry.spec),
  };
}

export function resolveCustomJudgeScenario(
  knowledgeRoot: string,
  name: string,
): ResolvedCustomAgentTask | null {
  const entry = resolveCustomScenarioEntry(knowledgeRoot, name);
  if (!entry) {
    return null;
  }

  const spec = entry.spec as Record<string, unknown>;
  const hasPrompt = typeof spec.taskPrompt === "string" && spec.taskPrompt.trim().length > 0;
  const hasRubric =
    (typeof spec.judgeRubric === "string" && spec.judgeRubric.trim().length > 0) ||
    (typeof spec.rubric === "string" && spec.rubric.trim().length > 0);
  if (!hasPrompt || !hasRubric) {
    return null;
  }

  return {
    name,
    path: entry.path,
    spec: normalizeAgentTaskSpec(spec),
  };
}

export function registerCustomScenarios(
  loaded: Map<string, CustomScenarioEntry>,
  provider?: LLMProvider,
): void {
  CUSTOM_SCENARIO_REGISTRY.clear();
  for (const name of Object.keys(CUSTOM_AGENT_TASK_REGISTRY)) {
    delete CUSTOM_AGENT_TASK_REGISTRY[name];
  }

  for (const [name, entry] of loaded) {
    CUSTOM_SCENARIO_REGISTRY.set(name, entry);
    if (entry.type === "agent_task") {
      const spec = normalizeAgentTaskSpec(entry.spec);
      CUSTOM_AGENT_TASK_REGISTRY[name] = () => createAgentTask({ spec, name, provider });
    }
  }
}
