/**
 * Simulation family codegen — generates JS source from a SimulationSpec (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/simulation_codegen.py.
 */

export function generateSimulationSource(
  spec: Record<string, unknown>,
  name: string,
): string {
  const description = String(spec.description ?? "");
  const envDescription = String(spec.environment_description ?? spec.environmentDescription ?? "");
  const initialStateDescription = String(spec.initial_state_description ?? spec.initialStateDescription ?? "");
  const successCriteria = (spec.success_criteria ?? spec.successCriteria ?? []) as string[];
  const failureModes = (spec.failure_modes ?? spec.failureModes ?? []) as string[];
  const maxSteps = Number(spec.max_steps ?? spec.maxSteps ?? 20);
  const actions = (spec.actions ?? []) as Array<{
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    preconditions: string[];
    effects: string[];
  }>;

  const actionSpecs = JSON.stringify(
    actions.map((a) => ({
      name: a.name,
      description: a.description,
      parameters: a.parameters ?? {},
      preconditions: a.preconditions ?? [],
      effects: a.effects ?? [],
    })),
    null,
    2,
  );

  const requiredActions = JSON.stringify(actions.map((a) => a.name));

  return `// Generated simulation scenario: ${name}
const ACTIONS = ${actionSpecs};
const REQUIRED_ACTIONS = ${requiredActions};

const scenario = {
  name: ${JSON.stringify(name)},

  describeScenario() {
    return ${JSON.stringify(description)};
  },

  describeEnvironment() {
    return {
      name: ${JSON.stringify(name)},
      description: ${JSON.stringify(envDescription)},
      availableActions: ACTIONS,
      initialStateDescription: ${JSON.stringify(initialStateDescription)},
      successCriteria: ${JSON.stringify(successCriteria)},
      failureModes: ${JSON.stringify(failureModes)},
    };
  },

  initialState(seed) {
    return {
      seed: seed || 0,
      step: 0,
      completedActions: [],
      failedActions: [],
      timeline: [],
      terminal: false,
    };
  },

  getAvailableActions(state) {
    const completed = new Set(state.completedActions || []);
    return ACTIONS.filter((a) => !completed.has(a.name));
  },

  executeAction(state, action) {
    const specs = Object.fromEntries(ACTIONS.map((a) => [a.name, a]));
    const spec = specs[action.name];
    const nextState = {
      ...state,
      step: (state.step || 0) + 1,
      timeline: [...(state.timeline || [])],
      completedActions: [...(state.completedActions || [])],
      failedActions: [...(state.failedActions || [])],
    };

    if (!spec) {
      nextState.failedActions.push(action.name);
      return {
        result: { success: false, output: "", stateChanges: {}, error: "unknown action: " + action.name },
        state: nextState,
      };
    }

    const completed = new Set(state.completedActions || []);
    for (const req of spec.preconditions || []) {
      if (!completed.has(req)) {
        nextState.failedActions.push(action.name);
        return {
          result: { success: false, output: "", stateChanges: {}, error: "precondition not met: " + req },
          state: nextState,
        };
      }
    }

    nextState.completedActions.push(action.name);
    nextState.timeline.push({ action: action.name, parameters: action.parameters || {} });
    return {
      result: {
        success: true,
        output: "executed " + action.name,
        stateChanges: { completedActions: [...nextState.completedActions] },
        sideEffects: [action.name],
      },
      state: nextState,
    };
  },

  isTerminal(state) {
    const required = new Set(REQUIRED_ACTIONS);
    const completed = new Set(state.completedActions || []);
    const allDone = [...required].every((r) => completed.has(r));
    return allDone || (state.step || 0) >= ${maxSteps};
  },

  getResult(state, trace) {
    const required = new Set(REQUIRED_ACTIONS);
    const completed = new Set(state.completedActions || []);
    const matching = [...required].filter((r) => completed.has(r)).length;
    const completion = required.size > 0 ? matching / required.size : 1.0;
    const records = trace?.records || [];
    const successes = records.filter((r) => r.result?.success).length;
    const successRate = records.length > 0 ? successes / records.length : 1.0;
    const failures = records.length - successes;
    const recovery = failures === 0 ? 1.0 : Math.max(0.2, 1.0 - failures / Math.max(records.length, 1));
    const score = Math.round((completion * 0.5 + successRate * 0.3 + recovery * 0.2) * 10000) / 10000;
    return {
      score,
      reasoning: "Completed " + matching + " of " + required.size + " required actions.",
      dimensionScores: {
        completion: Math.round(completion * 10000) / 10000,
        ordering: Math.round(successRate * 10000) / 10000,
        recovery: Math.round(recovery * 10000) / 10000,
      },
    };
  },

  getRubric() {
    return "Evaluate on completion, correct dependency ordering, and recovery quality.";
  },

  maxSteps() {
    return ${maxSteps};
  },
};

module.exports = { scenario };
`;
}
