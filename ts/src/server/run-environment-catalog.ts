import type { CustomScenarioEntry } from "../scenarios/custom-loader.js";
import { assertFamilyContract } from "../scenarios/family-interfaces.js";
import type { ScenarioInterface } from "../scenarios/game-interface.js";
import type { EnvironmentInfo } from "./run-manager.js";

export interface EnvironmentScenarioInfo {
  name: string;
  description: string;
}

type ScenarioClass = new () => ScenarioInterface;

export function describeCustomScenarioEntry(entry: CustomScenarioEntry): string {
  if (entry.type === "agent_task") {
    const taskPrompt = typeof entry.spec.taskPrompt === "string"
      ? entry.spec.taskPrompt
      : entry.name;
    return `Custom agent task: ${taskPrompt} (saved for custom-scenario tooling; not runnable via /run yet)`;
  }
  const description = typeof entry.spec.description === "string"
    ? entry.spec.description
    : `Custom ${entry.type} scenario`;
  if (entry.hasGeneratedSource) {
    return `${description} (generated custom scenario; runnable via /run)`;
  }
  return `${description} (saved custom scenario; not runnable via /run yet)`;
}

export function listBuiltinScenarioInfo(opts: {
  builtinScenarioNames: string[];
  getBuiltinScenarioClass: (name: string) => ScenarioClass | undefined;
}): EnvironmentScenarioInfo[] {
  return opts.builtinScenarioNames.map((name) => {
    const ScenarioClass = opts.getBuiltinScenarioClass(name);
    if (!ScenarioClass) {
      throw new Error(`Unknown built-in scenario: ${name}`);
    }
    const instance = new ScenarioClass();
    assertFamilyContract(instance, "game", `scenario '${name}'`);
    return { name, description: instance.describeRules() };
  });
}

export function listCustomScenarioInfo(opts: {
  customScenarios: Map<string, CustomScenarioEntry>;
  builtinScenarioNames: string[];
}): EnvironmentScenarioInfo[] {
  const builtin = new Set(opts.builtinScenarioNames);
  return [...opts.customScenarios.values()]
    .filter((entry) => !builtin.has(entry.name))
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((entry) => ({
      name: entry.name,
      description: describeCustomScenarioEntry(entry),
    }));
}

export function buildEnvironmentInfo(opts: {
  builtinScenarioNames: string[];
  getBuiltinScenarioClass: (name: string) => ScenarioClass | undefined;
  customScenarios: Map<string, CustomScenarioEntry>;
  activeProviderType: string | null;
}): EnvironmentInfo {
  return {
    scenarios: [
      ...listBuiltinScenarioInfo({
        builtinScenarioNames: opts.builtinScenarioNames,
        getBuiltinScenarioClass: opts.getBuiltinScenarioClass,
      }),
      ...listCustomScenarioInfo({
        customScenarios: opts.customScenarios,
        builtinScenarioNames: opts.builtinScenarioNames,
      }),
    ],
    executors: [
      { mode: "local", available: true, description: "Local subprocess execution" },
    ],
    currentExecutor: "local",
    agentProvider: opts.activeProviderType ?? "none",
  };
}
