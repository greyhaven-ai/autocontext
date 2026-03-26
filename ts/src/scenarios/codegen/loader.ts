/**
 * Dynamic scenario loader — loads generated JS source via ScenarioRuntime (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/loader.py but uses V8 isolates.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { ScenarioFamilyName } from "../families.js";
import { ScenarioRuntime, type ScenarioProxy, type ScenarioRuntimeOpts } from "./runtime.js";

/**
 * Load a custom scenario from its persisted source file.
 *
 * @param customDir - Path to the custom scenarios directory (e.g. knowledge/_custom_scenarios)
 * @param name - Scenario name (subdirectory name)
 * @param family - The scenario family
 * @param runtimeOpts - Optional runtime configuration
 * @returns A ScenarioProxy for calling scenario methods
 */
export async function loadCustomScenario(
  customDir: string,
  name: string,
  family: ScenarioFamilyName,
  runtimeOpts?: ScenarioRuntimeOpts,
): Promise<ScenarioProxy> {
  const scenarioDir = join(customDir, name);
  const sourcePath = join(scenarioDir, "scenario.js");

  if (!existsSync(sourcePath)) {
    throw new Error(`Custom scenario source not found: ${sourcePath}`);
  }

  const source = readFileSync(sourcePath, "utf-8");
  const runtime = new ScenarioRuntime(runtimeOpts);
  const proxy = await runtime.loadScenario(source, family, name);

  return {
    ...proxy,
    dispose() {
      runtime.dispose();
    },
  };
}

/**
 * Read the family from a scenario's persisted scenario_type.txt.
 */
export function readScenarioFamily(scenarioDir: string): ScenarioFamilyName | null {
  const typePath = join(scenarioDir, "scenario_type.txt");
  if (!existsSync(typePath)) return null;
  try {
    const marker = readFileSync(typePath, "utf-8").trim();
    // Reverse-map marker to family name
    const MARKER_TO_FAMILY: Record<string, ScenarioFamilyName> = {
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
    return MARKER_TO_FAMILY[marker] ?? null;
  } catch {
    return null;
  }
}
