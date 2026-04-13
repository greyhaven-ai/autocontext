import type {
  ExecutableScenario,
  ExecutionValidationContext,
} from "./execution-validator-contracts.js";

function recordValidationError(
  context: ExecutionValidationContext,
  message: string,
): void {
  context.errors.push(message);
}

function recordExecutedMethod(
  context: ExecutionValidationContext,
  method: string,
): void {
  context.executedMethods.push(method);
}

function validateStringMethod(
  scenario: ExecutableScenario,
  method: string,
  args: unknown[],
  context: ExecutionValidationContext,
  invalidMessage: string,
  allowEmpty = true,
): void {
  try {
    const value = scenario[method](...args);
    if (typeof value !== "string" || (!allowEmpty && value.length === 0)) {
      recordValidationError(context, invalidMessage);
      return;
    }
    recordExecutedMethod(context, method);
  } catch (error) {
    recordValidationError(
      context,
      `${method} crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

export async function validateAgentTaskScenario(
  scenario: ExecutableScenario,
  state: Record<string, unknown>,
  context: ExecutionValidationContext,
): Promise<void> {
  validateStringMethod(
    scenario,
    "describeTask",
    [],
    context,
    "describeTask must return a non-empty string",
    false,
  );
  validateStringMethod(
    scenario,
    "getTaskPrompt",
    [state],
    context,
    "getTaskPrompt must return a non-empty string",
    false,
  );
  validateStringMethod(
    scenario,
    "getRubric",
    [],
    context,
    "getRubric must return a string",
  );

  try {
    const result = await Promise.resolve(scenario.evaluateOutput("test output", state));
    if (result == null || typeof result !== "object") {
      recordValidationError(context, "evaluateOutput must return an object");
      return;
    }
    const evaluation = result as Record<string, unknown>;
    if (typeof evaluation.score !== "number") {
      recordValidationError(context, "evaluateOutput result.score must be a number");
    }
    recordExecutedMethod(context, "evaluateOutput");
  } catch (error) {
    recordValidationError(
      context,
      `evaluateOutput crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

export function validateSimulationLikeScenario(
  scenario: ExecutableScenario,
  state: Record<string, unknown>,
  context: ExecutionValidationContext,
): void {
  validateStringMethod(
    scenario,
    "describeScenario",
    [],
    context,
    "describeScenario must return a string",
  );

  try {
    const environment = scenario.describeEnvironment();
    if (environment == null || typeof environment !== "object") {
      recordValidationError(context, "describeEnvironment must return an object");
    } else {
      recordExecutedMethod(context, "describeEnvironment");
    }
  } catch (error) {
    recordValidationError(
      context,
      `describeEnvironment crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  validateStringMethod(
    scenario,
    "getRubric",
    [],
    context,
    "getRubric must return a string",
  );

  let actions: Array<{ name: string }> = [];
  try {
    const result = scenario.getAvailableActions(state);
    if (!Array.isArray(result)) {
      recordValidationError(context, "getAvailableActions must return an array");
    } else {
      actions = result as Array<{ name: string }>;
      if (actions.length === 0) {
        recordValidationError(
          context,
          "getAvailableActions must return at least one action for initial state",
        );
      }
      recordExecutedMethod(context, "getAvailableActions");
    }
  } catch (error) {
    recordValidationError(
      context,
      `getAvailableActions crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  let postActionState = state;
  if (actions.length > 0) {
    try {
      const actionResult = scenario.executeAction(state, {
        name: actions[0].name,
        parameters: {},
      });
      if (actionResult == null || typeof actionResult !== "object") {
        recordValidationError(context, "executeAction must return an object with result and state");
      } else {
        const result = actionResult as Record<string, unknown>;
        if (result.state && typeof result.state === "object") {
          postActionState = result.state as Record<string, unknown>;
        }
        recordExecutedMethod(context, "executeAction");
      }
    } catch (error) {
      recordValidationError(
        context,
        `executeAction crashed: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  try {
    const terminal = scenario.isTerminal(postActionState);
    if (typeof terminal !== "boolean") {
      recordValidationError(context, "isTerminal must return a boolean");
    } else {
      recordExecutedMethod(context, "isTerminal");
    }
  } catch (error) {
    recordValidationError(
      context,
      `isTerminal crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  try {
    const result = scenario.getResult(postActionState, { records: [] });
    if (result == null || typeof result !== "object") {
      recordValidationError(context, "getResult must return an object");
    } else {
      const payload = result as Record<string, unknown>;
      if (typeof payload.score !== "number") {
        recordValidationError(context, "getResult score must be a number, got: " + typeof payload.score);
      }
      recordExecutedMethod(context, "getResult");
    }
  } catch (error) {
    recordValidationError(
      context,
      `getResult crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

export function validateOperatorLoopScenario(
  scenario: ExecutableScenario,
  state: Record<string, unknown>,
  context: ExecutionValidationContext,
): void {
  validateSimulationLikeScenario(scenario, state, context);

  try {
    const clarified = scenario.requestClarification(state, {
      question: "What additional information is required?",
      urgency: "medium",
    });
    if (clarified == null || typeof clarified !== "object") {
      recordValidationError(context, "requestClarification must return an object state");
    } else {
      recordExecutedMethod(context, "requestClarification");
    }
  } catch (error) {
    recordValidationError(
      context,
      `requestClarification crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  try {
    const escalated = scenario.escalate(state, {
      reason: "Validation checkpoint",
      severity: "high",
      wasNecessary: true,
    });
    if (escalated == null || typeof escalated !== "object") {
      recordValidationError(context, "escalate must return an object state");
    } else {
      recordExecutedMethod(context, "escalate");
    }
  } catch (error) {
    recordValidationError(
      context,
      `escalate crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

export function validateArtifactEditingScenario(
  scenario: ExecutableScenario,
  state: Record<string, unknown>,
  context: ExecutionValidationContext,
): void {
  validateStringMethod(
    scenario,
    "describeTask",
    [],
    context,
    "describeTask must return a non-empty string",
    false,
  );

  let artifacts: Array<Record<string, unknown>> = [];
  try {
    const result = scenario.initialArtifacts();
    if (!Array.isArray(result)) {
      recordValidationError(context, "initialArtifacts must return an array");
    } else {
      artifacts = result as Array<Record<string, unknown>>;
      if (artifacts.length === 0) {
        recordValidationError(context, "initialArtifacts must return at least one artifact");
      }
      recordExecutedMethod(context, "initialArtifacts");
    }
  } catch (error) {
    recordValidationError(
      context,
      `initialArtifacts crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  validateStringMethod(
    scenario,
    "getRubric",
    [],
    context,
    "getRubric must return a string",
  );

  try {
    const prompt = scenario.getEditPrompt(artifacts, state);
    if (typeof prompt !== "string") {
      recordValidationError(context, "getEditPrompt must return a string");
    } else {
      recordExecutedMethod(context, "getEditPrompt");
    }
  } catch (error) {
    recordValidationError(
      context,
      `getEditPrompt crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  try {
    const artifact = artifacts[0] ?? {
      name: "__validation__",
      content: "",
      format: "text",
    };
    const validation = scenario.validateArtifact(artifact);
    if (validation == null || typeof validation !== "object") {
      recordValidationError(context, "validateArtifact must return an object");
    } else {
      recordExecutedMethod(context, "validateArtifact");
    }
  } catch (error) {
    recordValidationError(
      context,
      `validateArtifact crashed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}
