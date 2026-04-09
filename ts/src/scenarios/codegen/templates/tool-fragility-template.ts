export const TOOL_FRAGILITY_SCENARIO_TEMPLATE = String.raw`// Generated tool_fragility scenario: __SCENARIO_NAME_COMMENT__
const ACTIONS = __ACTIONS__;
const TOOL_CONTRACTS = __TOOL_CONTRACTS__;

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
      driftLog: [],
      driftInjected: false,
      attributions: [],
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
    };
    const spec = ACTIONS.find((candidate) => candidate.name === action.name);
    if (!spec) {
      nextState.failedActions.push(action.name);
      return {
        result: { success: false, output: "", stateChanges: {}, error: "unknown action" },
        state: nextState,
      };
    }
    if (state.driftInjected) {
      const affected = TOOL_CONTRACTS.find((contract) => contract.toolName === action.name);
      if (affected) {
        nextState.failedActions.push(action.name);
        return {
          result: { success: false, output: affected.driftBehavior, stateChanges: {}, error: "tool drift" },
          state: nextState,
        };
      }
    }
    nextState.completedActions.push(action.name);
    nextState.timeline.push({ action: action.name, parameters: action.parameters || {} });
    return {
      result: { success: true, output: "executed " + action.name, stateChanges: {} },
      state: nextState,
    };
  },

  isTerminal(state) {
    return (state.step || 0) >= __MAX_STEPS__;
  },

  getResult(state, trace) {
    const records = trace?.records || [];
    const successes = records.filter((record) => record.result?.success).length;
    const driftDetected = (state.attributions || []).length > 0;
    const adaptations = (state.completedActions || []).length;
    const detectionScore = driftDetected ? 1 : 0;
    const adaptScore = ACTIONS.length > 0 ? Math.min(1, adaptations / ACTIONS.length) : 1;
    const score = Math.round((detectionScore * 0.4 + adaptScore * 0.4 + (successes / Math.max(records.length, 1)) * 0.2) * 10000) / 10000;
    return {
      score,
      reasoning: "Drift " + (driftDetected ? "detected" : "undetected") + ", " + adaptations + " adaptations",
      dimensionScores: {
        driftDetection: detectionScore,
        adaptation: Math.round(adaptScore * 10000) / 10000,
      },
    };
  },

  getToolContracts() {
    return TOOL_CONTRACTS.map((contract) => ({ ...contract }));
  },

  getDriftLog(state) {
    return state.driftLog || [];
  },

  injectDrift(state, toolName) {
    return {
      ...state,
      driftInjected: true,
      driftLog: [...(state.driftLog || []), { toolName, timestamp: Date.now() }],
    };
  },

  attributeFailure(state, toolName, reason) {
    return {
      ...state,
      attributions: [...(state.attributions || []), { toolName, reason }],
    };
  },

  getRubric() {
    return "Evaluate drift detection accuracy, failure attribution, and adaptation quality.";
  },

  maxSteps() {
    return __MAX_STEPS__;
  },
};

module.exports = { scenario };
`;
