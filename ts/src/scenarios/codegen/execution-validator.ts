/**
 * Deep execution validation for generated scenario code (AC-442).
 *
 * Goes beyond AST/method-signature checks: actually runs the generated code
 * and verifies that initialState(), getAvailableActions(), executeAction(),
 * isTerminal(), and getResult() produce valid outputs.
 *
 * Catches logic errors that pass syntax validation but crash at runtime.
 */

export type {
  ExecutableScenario,
  ExecutionValidationContext,
  ExecutionValidationResult,
} from "./execution-validator-contracts.js";
import {
  buildExecutionValidationResult,
  getMissingRequiredMethods,
  loadGeneratedScenario,
  validateInitialScenarioState,
} from "./execution-validator-core-workflow.js";
import {
  validateAgentTaskScenario,
  validateArtifactEditingScenario,
  validateOperatorLoopScenario,
  validateSimulationLikeScenario,
} from "./execution-validator-family-workflow.js";

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
  _name: string,
) {
  const start = performance.now();
  const context = {
    errors: [] as string[],
    executedMethods: [] as string[],
  };

  const loaded = loadGeneratedScenario(source);
  if (!loaded.scenario) {
    if (loaded.error) {
      context.errors.push(loaded.error);
    }
    return buildExecutionValidationResult(start, context);
  }

  const missing = getMissingRequiredMethods(loaded.scenario, family);
  if (missing.length > 0) {
    context.errors.push(`missing required methods: ${missing.join(", ")}`);
    return buildExecutionValidationResult(start, context);
  }

  const state = validateInitialScenarioState(loaded.scenario, context);
  if (!state) {
    return buildExecutionValidationResult(start, context);
  }

  if (family === "agent_task") {
    await validateAgentTaskScenario(loaded.scenario, state, context);
  } else if (family === "operator_loop") {
    validateOperatorLoopScenario(loaded.scenario, state, context);
  } else if (family === "artifact_editing") {
    validateArtifactEditingScenario(loaded.scenario, state, context);
  } else {
    validateSimulationLikeScenario(loaded.scenario, state, context);
  }

  return buildExecutionValidationResult(start, context);
}
