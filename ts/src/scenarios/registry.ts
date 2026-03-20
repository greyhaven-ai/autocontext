/**
 * Scenario registry — dual-interface guards (AC-343 Task 7).
 * Mirrors Python's autocontext/scenarios/__init__.py.
 */

import type { ScenarioInterface } from "./game-interface.js";
import { GridCtfScenario } from "./grid-ctf.js";

type ScenarioFactory = new () => ScenarioInterface;

export const SCENARIO_REGISTRY: Record<string, ScenarioFactory> = {
  grid_ctf: GridCtfScenario,
};

/**
 * Type guard: true if obj implements ScenarioInterface (game scenario).
 */
export function isGameScenario(obj: unknown): obj is ScenarioInterface {
  if (!obj || typeof obj !== "object") return false;
  const o = obj as Record<string, unknown>;
  return (
    typeof o.describeRules === "function" &&
    typeof o.initialState === "function" &&
    typeof o.step === "function" &&
    typeof o.isTerminal === "function" &&
    typeof o.getResult === "function" &&
    typeof o.executeMatch === "function"
  );
}

/**
 * Type guard: true if obj implements AgentTaskInterface.
 */
export function isAgentTask(obj: unknown): boolean {
  if (!obj || typeof obj !== "object") return false;
  const o = obj as Record<string, unknown>;
  return (
    typeof o.getTaskPrompt === "function" &&
    typeof o.evaluateOutput === "function" &&
    typeof o.getRubric === "function" &&
    typeof o.initialState === "function" &&
    typeof o.describeTask === "function"
  );
}
