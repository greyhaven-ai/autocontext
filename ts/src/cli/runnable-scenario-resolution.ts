import { join } from "node:path";

import type { ScenarioInterface } from "../scenarios/game-interface.js";
import type { CustomScenarioEntry } from "../scenarios/custom-loader.js";
import { loadCustomScenarios } from "../scenarios/custom-loader.js";
import { createPersistedParametricScenarioClass } from "../scenarios/persisted-parametric-scenario.js";

type ScenarioClass = new () => ScenarioInterface;

function listAvailableScenarioNames(
  builtinScenarios: Record<string, ScenarioClass>,
  customScenarios: Map<string, CustomScenarioEntry>,
): string {
  return [...new Set([...Object.keys(builtinScenarios), ...customScenarios.keys()])]
    .sort()
    .join(", ");
}

export function resolveRunnableScenarioClass(opts: {
  scenarioName: string;
  builtinScenarios: Record<string, ScenarioClass>;
  knowledgeRoot: string;
  loadPersistedCustomScenarios?: (customDir: string) => Map<string, CustomScenarioEntry>;
  createParametricScenarioClass?: typeof createPersistedParametricScenarioClass;
}): ScenarioClass {
  const builtin = opts.builtinScenarios[opts.scenarioName];
  if (builtin) {
    return builtin;
  }

  const customDir = join(opts.knowledgeRoot, "_custom_scenarios");
  const customScenarios = (opts.loadPersistedCustomScenarios ?? loadCustomScenarios)(customDir);
  const entry = customScenarios.get(opts.scenarioName);
  if (!entry) {
    throw new Error(
      `Unknown scenario: ${opts.scenarioName}. Available: ${listAvailableScenarioNames(opts.builtinScenarios, customScenarios)}`,
    );
  }

  if (entry.type === "parametric") {
    return (opts.createParametricScenarioClass ?? createPersistedParametricScenarioClass)(
      opts.scenarioName,
      entry.spec,
    );
  }

  throw new Error(
    `Scenario '${opts.scenarioName}' is a saved custom ${entry.type} scenario. ` +
      "Run and benchmark currently support built-in scenarios and saved parametric scenarios by name.",
  );
}
