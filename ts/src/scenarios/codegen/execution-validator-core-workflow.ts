import { SIMULATION_LIKE_FAMILIES } from "../families.js";
import type {
  ExecutableScenario,
  ExecutionValidationContext,
  ExecutionValidationResult,
} from "./execution-validator-contracts.js";

export function buildExecutionValidationResult(
  start: number,
  context: ExecutionValidationContext,
): ExecutionValidationResult {
  return {
    valid: context.errors.length === 0,
    errors: context.errors,
    executedMethods: context.executedMethods,
    durationMs: performance.now() - start,
  };
}

export function loadGeneratedScenario(source: string): {
  scenario: ExecutableScenario | null;
  error?: string;
} {
  try {
    const moduleObj = { exports: {} as Record<string, unknown> };
    const fn = new Function("module", "exports", source);
    fn(moduleObj, moduleObj.exports);
    const scenario =
      ((moduleObj.exports as { scenario?: Record<string, unknown> }).scenario as ExecutableScenario)
      ?? (moduleObj.exports as ExecutableScenario);
    if (!scenario || typeof scenario !== "object") {
      return {
        scenario: null,
        error: "generated code does not export a scenario object",
      };
    }
    return { scenario };
  } catch (error) {
    return {
      scenario: null,
      error: `failed to load generated code: ${error instanceof Error ? error.message : String(error)}`,
    };
  }
}

export function getRequiredMethods(family: string): string[] {
  if (family === "agent_task") {
    return [
      "getTaskPrompt",
      "getRubric",
      "describeTask",
      "initialState",
      "evaluateOutput",
    ];
  }
  if (family === "artifact_editing") {
    return [
      "describeTask",
      "getRubric",
      "initialArtifacts",
      "getEditPrompt",
      "validateArtifact",
      "initialState",
    ];
  }
  if (family === "operator_loop") {
    return [
      "describeScenario",
      "describeEnvironment",
      "initialState",
      "getAvailableActions",
      "executeAction",
      "isTerminal",
      "getResult",
      "getRubric",
      "requestClarification",
      "escalate",
    ];
  }
  if (SIMULATION_LIKE_FAMILIES.has(family)) {
    return [
      "describeScenario",
      "describeEnvironment",
      "initialState",
      "getAvailableActions",
      "executeAction",
      "isTerminal",
      "getResult",
      "getRubric",
    ];
  }
  return ["initialState"];
}

export function getMissingRequiredMethods(
  scenario: ExecutableScenario,
  family: string,
): string[] {
  return getRequiredMethods(family).filter((method) => typeof scenario[method] !== "function");
}

export function validateInitialScenarioState(
  scenario: ExecutableScenario,
  context: ExecutionValidationContext,
): Record<string, unknown> | null {
  try {
    const result = scenario.initialState(42);
    if (result == null || typeof result !== "object" || Array.isArray(result)) {
      context.errors.push("initialState must return an object, got: " + typeof result);
      return null;
    }
    context.executedMethods.push("initialState");
    return result as Record<string, unknown>;
  } catch (error) {
    context.errors.push(
      `initialState crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
    return null;
  }
}
