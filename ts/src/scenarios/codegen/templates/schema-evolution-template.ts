export const SCHEMA_EVOLUTION_SCENARIO_TEMPLATE = String.raw`// Generated schema_evolution scenario: __SCENARIO_NAME_COMMENT__
const ACTIONS = __ACTIONS__;
const MUTATIONS = __MUTATIONS__;

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
      schemaVersion: 0,
      mutationLog: [],
      staleDetections: 0,
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
    nextState.completedActions.push(action.name);
    nextState.timeline.push({ action: action.name, parameters: action.parameters || {} });
    return {
      result: { success: true, output: "executed " + action.name, stateChanges: {} },
      state: nextState,
    };
  },

  isTerminal(state) {
    return (state.schemaVersion || 0) >= MUTATIONS.length || (state.step || 0) >= __MAX_STEPS__;
  },

  getResult(state, trace) {
    const versionsHandled = state.schemaVersion || 0;
    const coverage = MUTATIONS.length > 0 ? versionsHandled / MUTATIONS.length : 1;
    const detections = state.staleDetections || 0;
    const detectionRate = MUTATIONS.length > 0 ? Math.min(1, detections / MUTATIONS.length) : 1;
    const score = Math.round((coverage * 0.5 + detectionRate * 0.5) * 10000) / 10000;
    return {
      score,
      reasoning: versionsHandled + "/" + MUTATIONS.length + " versions handled",
      dimensionScores: {
        schemaCoverage: Math.round(coverage * 10000) / 10000,
        staleDetection: Math.round(detectionRate * 10000) / 10000,
      },
    };
  },

  getMutations() {
    return MUTATIONS.map((mutation) => ({ ...mutation }));
  },

  getSchemaVersion(state) {
    return state.schemaVersion || 0;
  },

  getMutationLog(state) {
    return state.mutationLog || [];
  },

  applyMutation(state, mutation) {
    return {
      ...state,
      schemaVersion: (state.schemaVersion || 0) + 1,
      mutationLog: [...(state.mutationLog || []), mutation],
      staleDetections: (state.staleDetections || 0) + 1,
    };
  },

  getRubric() {
    return "Evaluate schema migration handling, stale context detection, and adaptation quality.";
  },

  maxSteps() {
    return __MAX_STEPS__;
  },
};

module.exports = { scenario };
`;
