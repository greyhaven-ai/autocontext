/**
 * Deep execution validation for generated scenario code (AC-442).
 *
 * Goes beyond AST/method-signature checks: actually runs the generated code
 * and verifies that initialState(), getAvailableActions(), executeAction(),
 * isTerminal(), and getResult() produce valid outputs.
 *
 * Catches logic errors that pass syntax validation but crash at runtime.
 */

import type { ScenarioFamilyName } from "../families.js";

export interface ExecutionValidationResult {
  /** Whether the generated code passed all execution checks. */
  valid: boolean;
  /** Error descriptions for any failures. */
  errors: string[];
  /** Methods that were successfully called during validation. */
  executedMethods: string[];
  /** Duration of the validation run in milliseconds. */
  durationMs: number;
}

/**
 * Required methods per family for execution validation.
 * Methods listed here will be called during validation.
 */
const SIMULATION_LIKE_FAMILIES = new Set([
  "simulation", "investigation", "workflow", "negotiation",
  "schema_evolution", "tool_fragility", "operator_loop", "coordination",
]);

/**
 * Validate generated scenario code by actually executing it.
 *
 * Runs the code, calls key methods, and verifies return shapes.
 * Does NOT require secure-exec — uses plain eval for speed since
 * this is validation, not untrusted execution.
 */
export async function validateGeneratedScenario(
  source: string,
  family: string,
  name: string,
): Promise<ExecutionValidationResult> {
  const start = performance.now();
  const errors: string[] = [];
  const executedMethods: string[] = [];

  // Step 1: Load the module
  let scenario: Record<string, (...args: unknown[]) => unknown>;
  try {
    const moduleObj = { exports: {} as Record<string, unknown> };
    const fn = new Function("module", "exports", source);
    fn(moduleObj, moduleObj.exports);
    scenario = (moduleObj.exports as { scenario?: Record<string, unknown> }).scenario as
      Record<string, (...args: unknown[]) => unknown> ??
      moduleObj.exports as Record<string, (...args: unknown[]) => unknown>;
    if (!scenario || typeof scenario !== "object") {
      errors.push("generated code does not export a scenario object");
      return { valid: false, errors, executedMethods, durationMs: performance.now() - start };
    }
  } catch (err) {
    errors.push(`failed to load generated code: ${err instanceof Error ? err.message : String(err)}`);
    return { valid: false, errors, executedMethods, durationMs: performance.now() - start };
  }

  // Step 2: Check required methods exist
  const requiredMethods = getRequiredMethods(family);
  const missing = requiredMethods.filter((m) => typeof scenario[m] !== "function");
  if (missing.length > 0) {
    errors.push(`missing required methods: ${missing.join(", ")}`);
    return { valid: false, errors, executedMethods, durationMs: performance.now() - start };
  }

  // Step 3: Execute initialState
  let state: Record<string, unknown>;
  try {
    const result = scenario.initialState(42);
    if (result == null || typeof result !== "object" || Array.isArray(result)) {
      errors.push("initialState must return an object, got: " + typeof result);
      return { valid: false, errors, executedMethods, durationMs: performance.now() - start };
    }
    state = result as Record<string, unknown>;
    executedMethods.push("initialState");
  } catch (err) {
    errors.push(`initialState crashed: ${err instanceof Error ? err.message : String(err)}`);
    return { valid: false, errors, executedMethods, durationMs: performance.now() - start };
  }

  // Step 4: Family-specific validation
  if (family === "agent_task") {
    await validateAgentTask(scenario, state, errors, executedMethods);
  } else if (SIMULATION_LIKE_FAMILIES.has(family) || family === "game") {
    validateSimulationLike(scenario, state, errors, executedMethods);
  } else if (family === "artifact_editing") {
    validateArtifactEditing(scenario, state, errors, executedMethods);
  }

  return {
    valid: errors.length === 0,
    errors,
    executedMethods,
    durationMs: performance.now() - start,
  };
}

// ---------------------------------------------------------------------------
// Per-family validation
// ---------------------------------------------------------------------------

function getRequiredMethods(family: string): string[] {
  if (family === "agent_task") {
    return ["getTaskPrompt", "getRubric", "describeTask", "initialState", "evaluateOutput"];
  }
  if (family === "artifact_editing") {
    return ["describeTask", "getRubric", "initialArtifacts", "getEditPrompt", "validateArtifact", "initialState"];
  }
  if (SIMULATION_LIKE_FAMILIES.has(family)) {
    return [
      "describeScenario", "describeEnvironment", "initialState",
      "getAvailableActions", "executeAction", "isTerminal", "getResult", "getRubric",
    ];
  }
  return ["initialState"];
}

async function validateAgentTask(
  scenario: Record<string, (...args: unknown[]) => unknown>,
  state: Record<string, unknown>,
  errors: string[],
  executedMethods: string[],
): Promise<void> {
  // describeTask
  try {
    const description = scenario.describeTask();
    if (typeof description !== "string" || description.length === 0) {
      errors.push("describeTask must return a non-empty string");
    } else {
      executedMethods.push("describeTask");
    }
  } catch (err) {
    errors.push(`describeTask crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // getTaskPrompt
  try {
    const prompt = scenario.getTaskPrompt(state);
    if (typeof prompt !== "string" || prompt.length === 0) {
      errors.push("getTaskPrompt must return a non-empty string");
    } else {
      executedMethods.push("getTaskPrompt");
    }
  } catch (err) {
    errors.push(`getTaskPrompt crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // getRubric
  try {
    const rubric = scenario.getRubric();
    if (typeof rubric !== "string") {
      errors.push("getRubric must return a string");
    } else {
      executedMethods.push("getRubric");
    }
  } catch (err) {
    errors.push(`getRubric crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // evaluateOutput
  try {
    const evalResult = await Promise.resolve(scenario.evaluateOutput("test output", state));
    if (evalResult == null || typeof evalResult !== "object") {
      errors.push("evaluateOutput must return an object");
    } else {
      const r = evalResult as Record<string, unknown>;
      if (typeof r.score !== "number") {
        errors.push("evaluateOutput result.score must be a number");
      }
      executedMethods.push("evaluateOutput");
    }
  } catch (err) {
    errors.push(`evaluateOutput crashed: ${err instanceof Error ? err.message : String(err)}`);
  }
}

function validateSimulationLike(
  scenario: Record<string, (...args: unknown[]) => unknown>,
  state: Record<string, unknown>,
  errors: string[],
  executedMethods: string[],
): void {
  // describeScenario
  try {
    const desc = scenario.describeScenario();
    if (typeof desc !== "string") {
      errors.push("describeScenario must return a string");
    } else {
      executedMethods.push("describeScenario");
    }
  } catch (err) {
    errors.push(`describeScenario crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // describeEnvironment
  try {
    const environment = scenario.describeEnvironment();
    if (environment == null || typeof environment !== "object") {
      errors.push("describeEnvironment must return an object");
    } else {
      executedMethods.push("describeEnvironment");
    }
  } catch (err) {
    errors.push(`describeEnvironment crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // getRubric
  try {
    const rubric = scenario.getRubric();
    if (typeof rubric !== "string") {
      errors.push("getRubric must return a string");
    } else {
      executedMethods.push("getRubric");
    }
  } catch (err) {
    errors.push(`getRubric crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // getAvailableActions
  let actions: Array<{ name: string }> = [];
  try {
    const result = scenario.getAvailableActions(state);
    if (!Array.isArray(result)) {
      errors.push("getAvailableActions must return an array");
    } else {
      actions = result as Array<{ name: string }>;
      executedMethods.push("getAvailableActions");
    }
  } catch (err) {
    errors.push(`getAvailableActions crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // executeAction (if there are actions available)
  let postActionState = state;
  if (actions.length > 0) {
    try {
      const actionResult = scenario.executeAction(state, { name: actions[0].name, parameters: {} });
      if (actionResult == null || typeof actionResult !== "object") {
        errors.push("executeAction must return an object with result and state");
      } else {
        const r = actionResult as Record<string, unknown>;
        if (r.state && typeof r.state === "object") {
          postActionState = r.state as Record<string, unknown>;
        }
        executedMethods.push("executeAction");
      }
    } catch (err) {
      errors.push(`executeAction crashed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // isTerminal
  try {
    const terminal = scenario.isTerminal(postActionState);
    if (typeof terminal !== "boolean") {
      errors.push("isTerminal must return a boolean");
    } else {
      executedMethods.push("isTerminal");
    }
  } catch (err) {
    errors.push(`isTerminal crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // getResult
  try {
    const result = scenario.getResult(postActionState, { records: [] });
    if (result == null || typeof result !== "object") {
      errors.push("getResult must return an object");
    } else {
      const r = result as Record<string, unknown>;
      if (typeof r.score !== "number") {
        errors.push("getResult score must be a number, got: " + typeof r.score);
      }
      executedMethods.push("getResult");
    }
  } catch (err) {
    errors.push(`getResult crashed: ${err instanceof Error ? err.message : String(err)}`);
  }
}

function validateArtifactEditing(
  scenario: Record<string, (...args: unknown[]) => unknown>,
  state: Record<string, unknown>,
  errors: string[],
  executedMethods: string[],
): void {
  // describeTask
  try {
    const description = scenario.describeTask();
    if (typeof description !== "string" || description.length === 0) {
      errors.push("describeTask must return a non-empty string");
    } else {
      executedMethods.push("describeTask");
    }
  } catch (err) {
    errors.push(`describeTask crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // initialArtifacts
  let artifacts: Array<Record<string, unknown>> = [];
  try {
    const result = scenario.initialArtifacts();
    if (!Array.isArray(result)) {
      errors.push("initialArtifacts must return an array");
    } else {
      artifacts = result as Array<Record<string, unknown>>;
      executedMethods.push("initialArtifacts");
    }
  } catch (err) {
    errors.push(`initialArtifacts crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // getRubric
  try {
    const rubric = scenario.getRubric();
    if (typeof rubric !== "string") {
      errors.push("getRubric must return a string");
    } else {
      executedMethods.push("getRubric");
    }
  } catch (err) {
    errors.push(`getRubric crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // getEditPrompt
  try {
    const prompt = scenario.getEditPrompt(artifacts, state);
    if (typeof prompt !== "string") {
      errors.push("getEditPrompt must return a string");
    } else {
      executedMethods.push("getEditPrompt");
    }
  } catch (err) {
    errors.push(`getEditPrompt crashed: ${err instanceof Error ? err.message : String(err)}`);
  }

  // validateArtifact
  try {
    const artifact = artifacts[0] ?? { name: "__validation__", content: "", format: "text" };
    const validation = scenario.validateArtifact(artifact);
    if (validation == null || typeof validation !== "object") {
      errors.push("validateArtifact must return an object");
    } else {
      executedMethods.push("validateArtifact");
    }
  } catch (err) {
    errors.push(`validateArtifact crashed: ${err instanceof Error ? err.message : String(err)}`);
  }
}
