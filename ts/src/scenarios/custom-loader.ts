/**
 * Custom scenario loader — scan knowledge dir, load specs, register (AC-348 Task 29).
 * Mirrors Python's autocontext/scenarios/custom/registry.py.
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import type { AgentTaskInterface, LLMProvider } from "../types/index.js";
import { createAgentTask } from "./agent-task-factory.js";
import { parseRawSpec, type AgentTaskSpec } from "./agent-task-spec.js";

export interface CustomScenarioEntry {
  name: string;
  type: string;
  spec: Record<string, unknown>;
  path: string;
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
      calibrationExamples: (spec.calibrationExamples as Array<Record<string, unknown>> | null | undefined) ?? undefined,
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

    // Read scenario type (default to agent_task)
    const typePath = join(entryPath, "scenario_type.txt");
    let scenarioType = "agent_task";
    if (existsSync(typePath)) {
      try {
        scenarioType = readFileSync(typePath, "utf-8").trim();
      } catch {
        scenarioType = "agent_task";
      }
    }
    const specPath = join(entryPath, "spec.json");
    const agentTaskSpecPath = join(entryPath, "agent_task_spec.json");
    if (
      !existsSync(specPath)
      && !(scenarioType === "agent_task" && existsSync(agentTaskSpecPath))
    ) {
      continue;
    }

    // Read spec
    try {
      const specSourcePath = scenarioType === "agent_task" && existsSync(agentTaskSpecPath)
        ? agentTaskSpecPath
        : specPath;
      const specRaw = readFileSync(specSourcePath, "utf-8");
      const rawSpec = JSON.parse(specRaw) as Record<string, unknown>;
      const spec = scenarioType === "agent_task"
        ? normalizeAgentTaskSpec(rawSpec)
        : rawSpec;
      loaded.set(name, {
        name,
        type: scenarioType,
        spec,
        path: entryPath,
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
export function discoverAndRegisterCustomScenarios(
  knowledgeRoot: string,
  provider?: LLMProvider,
): number {
  const customDir = join(knowledgeRoot, "_custom_scenarios");
  const loaded = loadCustomScenarios(customDir);
  registerCustomScenarios(loaded, provider);
  return loaded.size;
}

export function resolveCustomAgentTask(
  knowledgeRoot: string,
  name: string,
): ResolvedCustomAgentTask | null {
  const customDir = join(knowledgeRoot, "_custom_scenarios");
  const entry = loadCustomScenarios(customDir).get(name);
  if (!entry || entry.type !== "agent_task") {
    return null;
  }
  return {
    name,
    path: entry.path,
    spec: normalizeAgentTaskSpec(entry.spec),
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
