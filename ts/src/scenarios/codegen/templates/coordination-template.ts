export const COORDINATION_SCENARIO_TEMPLATE = String.raw`// Generated coordination scenario: __SCENARIO_NAME_COMMENT__
const ACTIONS = __ACTIONS__;
const WORKERS = __WORKERS__;

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
      handoffLog: [],
      mergedOutputs: [],
      workerOutputs: Object.fromEntries(WORKERS.map((worker) => [worker.id, []])),
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
    return (state.step || 0) >= __MAX_STEPS__;
  },

  getResult(state, trace) {
    const records = trace?.records || [];
    const successes = records.filter((record) => record.result?.success).length;
    const handoffs = (state.handoffLog || []).length;
    const merges = (state.mergedOutputs || []).length;
    const coordScore = WORKERS.length > 1 ? Math.min(1, (handoffs + merges) / (WORKERS.length * 2)) : 1;
    const successRate = records.length > 0 ? successes / records.length : 1;
    const score = Math.round((coordScore * 0.5 + successRate * 0.5) * 10000) / 10000;
    return {
      score,
      reasoning: handoffs + " handoffs, " + merges + " merges",
      dimensionScores: {
        coordination: Math.round(coordScore * 10000) / 10000,
        successRate: Math.round(successRate * 10000) / 10000,
      },
    };
  },

  getWorkerContexts() {
    return WORKERS.map((worker) => ({ ...worker }));
  },

  getHandoffLog(state) {
    return state.handoffLog || [];
  },

  recordHandoff(state, fromWorker, toWorker, payload) {
    return {
      ...state,
      handoffLog: [...(state.handoffLog || []), { from: fromWorker, to: toWorker, payload, timestamp: Date.now() }],
    };
  },

  mergeOutputs(state, outputs) {
    const merged = Object.values(outputs).flat();
    return {
      ...state,
      mergedOutputs: [...(state.mergedOutputs || []), { outputs: merged, timestamp: Date.now() }],
    };
  },

  getRubric() {
    return "Evaluate handoff quality, merge correctness, and duplication avoidance.";
  },

  maxSteps() {
    return __MAX_STEPS__;
  },
};

module.exports = { scenario };
`;
