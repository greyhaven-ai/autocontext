export const NEGOTIATION_SCENARIO_TEMPLATE = String.raw`// Generated negotiation scenario: __SCENARIO_NAME_COMMENT__
const ACTIONS = __ACTIONS__;
const HIDDEN_PREFS = __HIDDEN_PREFS__;
const TOTAL_ROUNDS = __TOTAL_ROUNDS__;

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
      round: 0,
      offers: [],
      opponentModel: {},
    };
  },

  getAvailableActions(state) {
    return ACTIONS;
  },

  executeAction(state, action) {
    const nextState = {
      ...state,
      step: (state.step || 0) + 1,
      round: (state.round || 0) + 1,
      timeline: [...(state.timeline || [])],
      completedActions: [...(state.completedActions || [])],
      offers: [...(state.offers || [])],
    };
    nextState.completedActions.push(action.name);
    nextState.timeline.push({ action: action.name, parameters: action.parameters || {} });
    if (action.name === "offer" || action.name === "propose") {
      nextState.offers.push(action.parameters || {});
    }
    return {
      result: { success: true, output: "executed " + action.name, stateChanges: {} },
      state: nextState,
    };
  },

  isTerminal(state) {
    return (state.round || 0) >= TOTAL_ROUNDS || (state.step || 0) >= __MAX_STEPS__;
  },

  getResult(state, trace) {
    const rounds = state.round || 0;
    const offers = state.offers || [];
    const adaptations = new Set(offers.map((offer) => JSON.stringify(offer))).size;
    const adaptationScore = offers.length > 1 ? Math.min(1, adaptations / offers.length) : 0.5;
    const completionScore = Math.min(1, rounds / TOTAL_ROUNDS);
    const score = Math.round((completionScore * 0.5 + adaptationScore * 0.5) * 10000) / 10000;
    return {
      score,
      reasoning: rounds + " rounds, " + adaptations + " distinct offers",
      dimensionScores: {
        completion: Math.round(completionScore * 10000) / 10000,
        adaptation: Math.round(adaptationScore * 10000) / 10000,
      },
    };
  },

  getHiddenPreferences() {
    return { ...HIDDEN_PREFS };
  },

  getRounds() {
    return TOTAL_ROUNDS;
  },

  getOpponentModel(state) {
    return state.opponentModel || {};
  },

  updateOpponentModel(state, observation) {
    return { ...state, opponentModel: { ...(state.opponentModel || {}), ...observation } };
  },

  getRubric() {
    return "Evaluate negotiation on adaptation, opponent modeling, and outcome quality.";
  },

  maxSteps() {
    return __MAX_STEPS__;
  },
};

module.exports = { scenario };
`;
