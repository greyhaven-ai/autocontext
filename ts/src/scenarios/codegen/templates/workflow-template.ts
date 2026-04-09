export const WORKFLOW_SCENARIO_TEMPLATE = String.raw`// Generated workflow scenario: __SCENARIO_NAME_COMMENT__
const ACTIONS = __ACTIONS__;
const WORKFLOW_STEPS = __WORKFLOW_STEPS__;

const scenario = {
  name: __SCENARIO_NAME__,

  describeScenario() {
    return __DESCRIPTION__;
  },

  describeEnvironment() {
    return {
      name: __SCENARIO_NAME__,
      description: __ENV_DESCRIPTION__,
      availableActions: ACTIONS,
      initialStateDescription: __INITIAL_STATE_DESCRIPTION__,
      successCriteria: __SUCCESS_CRITERIA__,
      failureModes: __FAILURE_MODES__,
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
      completedSteps: [],
      compensations: [],
      sideEffects: [],
    };
  },

  getAvailableActions(state) {
    const completed = new Set(state.completedActions || []);
    return ACTIONS.filter((action) => !completed.has(action.name));
  },

  executeAction(state, action) {
    const nextState = {
      ...state,
      step: (state.step || 0) + 1,
      timeline: [...(state.timeline || [])],
      completedActions: [...(state.completedActions || [])],
      failedActions: [...(state.failedActions || [])],
      sideEffects: [...(state.sideEffects || [])],
    };
    const spec = ACTIONS.find((candidate) => candidate.name === action.name);
    if (!spec) {
      nextState.failedActions.push(action.name);
      return {
        result: { success: false, output: "", stateChanges: {}, error: "unknown action" },
        state: nextState,
      };
    }
    const completed = new Set(state.completedActions || []);
    for (const req of spec.preconditions || []) {
      if (!completed.has(req)) {
        nextState.failedActions.push(action.name);
        return {
          result: { success: false, output: "", stateChanges: {}, error: "precondition: " + req },
          state: nextState,
        };
      }
    }
    nextState.completedActions.push(action.name);
    nextState.timeline.push({ action: action.name, parameters: action.parameters || {} });
    const stepSpec = WORKFLOW_STEPS.find((step) => step.name === action.name);
    if (stepSpec?.sideEffects) {
      nextState.sideEffects.push(...stepSpec.sideEffects);
    }
    return {
      result: {
        success: true,
        output: "executed " + action.name,
        stateChanges: {},
        sideEffects: stepSpec?.sideEffects || [],
      },
      state: nextState,
    };
  },

  isTerminal(state) {
    const stepNames = new Set(WORKFLOW_STEPS.map((step) => step.name));
    const completed = new Set(state.completedActions || []);
    return [...stepNames].every((stepName) => completed.has(stepName)) || (state.step || 0) >= __MAX_STEPS__;
  },

  getResult(state, trace) {
    const stepNames = new Set(WORKFLOW_STEPS.map((step) => step.name));
    const completed = new Set(state.completedActions || []);
    const stepsCompleted = [...stepNames].filter((stepName) => completed.has(stepName)).length;
    const completion = stepNames.size > 0 ? stepsCompleted / stepNames.size : 1;
    const records = trace?.records || [];
    const failures = records.filter((record) => !record.result?.success).length;
    const recovery = failures === 0 ? 1 : Math.max(0.2, 1 - failures / Math.max(records.length, 1));
    const score = Math.round((completion * 0.5 + recovery * 0.3 + (records.length > 0 ? 0.2 : 0)) * 10000) / 10000;
    return {
      score,
      reasoning: stepsCompleted + "/" + stepNames.size + " steps, " + failures + " failures",
      dimensionScores: {
        completion: Math.round(completion * 10000) / 10000,
        recovery: Math.round(recovery * 10000) / 10000,
      },
    };
  },

  getWorkflowSteps() {
    return WORKFLOW_STEPS.map((step) => ({ ...step }));
  },

  executeStep(state, stepName) {
    const step = WORKFLOW_STEPS.find((candidate) => candidate.name === stepName);
    if (!step) {
      return { success: false, error: "unknown step: " + stepName };
    }
    return scenario.executeAction(state, { name: stepName, parameters: {} });
  },

  executeCompensation(state, stepName) {
    const step = WORKFLOW_STEPS.find((candidate) => candidate.name === stepName);
    if (!step?.compensationAction) {
      return { success: false, error: "no compensation for: " + stepName };
    }
    const nextState = {
      ...state,
      compensations: [...(state.compensations || []), stepName],
    };
    return {
      result: { success: true, output: "compensated " + stepName },
      state: nextState,
    };
  },

  getSideEffects(state) {
    return state.sideEffects || [];
  },

  getRubric() {
    return "Evaluate on workflow completion, compensation correctness, and side-effect tracking.";
  },

  maxSteps() {
    return __MAX_STEPS__;
  },
};

module.exports = { scenario };
`;
