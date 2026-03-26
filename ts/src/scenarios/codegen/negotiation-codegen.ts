/**
 * Negotiation family codegen (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/negotiation_codegen.py.
 */

export function generateNegotiationSource(
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
    name: string; description: string; parameters: Record<string, unknown>;
    preconditions: string[]; effects: string[];
  }>;
  const hiddenPreferences = (spec.hidden_preferences ?? spec.hiddenPreferences ?? {}) as Record<string, unknown>;
  const totalRounds = Number(spec.rounds ?? spec.totalRounds ?? 5);

  return `// Generated negotiation scenario: ${name}
const ACTIONS = ${JSON.stringify(actions, null, 2)};
const HIDDEN_PREFS = ${JSON.stringify(hiddenPreferences)};
const TOTAL_ROUNDS = ${totalRounds};

const scenario = {
  name: ${JSON.stringify(name)},
  describeScenario() { return ${JSON.stringify(description)}; },
  describeEnvironment() {
    return { name: ${JSON.stringify(name)}, description: ${JSON.stringify(envDescription)},
      availableActions: ACTIONS, initialStateDescription: ${JSON.stringify(initialStateDescription)},
      successCriteria: ${JSON.stringify(successCriteria)}, failureModes: ${JSON.stringify(failureModes)} };
  },
  initialState(seed) {
    return { seed: seed || 0, step: 0, completedActions: [], failedActions: [],
      timeline: [], terminal: false, round: 0, offers: [], opponentModel: {} };
  },
  getAvailableActions(state) { return ACTIONS; },
  executeAction(state, action) {
    const nextState = { ...state, step: (state.step || 0) + 1, round: (state.round || 0) + 1,
      timeline: [...(state.timeline || [])], completedActions: [...(state.completedActions || [])],
      offers: [...(state.offers || [])] };
    nextState.completedActions.push(action.name);
    nextState.timeline.push({ action: action.name, parameters: action.parameters || {} });
    if (action.name === "offer" || action.name === "propose") {
      nextState.offers.push(action.parameters || {});
    }
    return { result: { success: true, output: "executed " + action.name, stateChanges: {} }, state: nextState };
  },
  isTerminal(state) { return (state.round || 0) >= TOTAL_ROUNDS || (state.step || 0) >= ${maxSteps}; },
  getResult(state, trace) {
    const rounds = state.round || 0;
    const offers = state.offers || [];
    const adaptations = new Set(offers.map(o => JSON.stringify(o))).size;
    const adaptationScore = offers.length > 1 ? Math.min(1, adaptations / offers.length) : 0.5;
    const completionScore = Math.min(1, rounds / TOTAL_ROUNDS);
    const score = Math.round((completionScore * 0.5 + adaptationScore * 0.5) * 10000) / 10000;
    return { score, reasoning: rounds + " rounds, " + adaptations + " distinct offers",
      dimensionScores: { completion: Math.round(completionScore * 10000) / 10000, adaptation: Math.round(adaptationScore * 10000) / 10000 } };
  },
  getHiddenPreferences() { return { ...HIDDEN_PREFS }; },
  getRounds() { return TOTAL_ROUNDS; },
  getOpponentModel(state) { return state.opponentModel || {}; },
  updateOpponentModel(state, observation) {
    return { ...state, opponentModel: { ...(state.opponentModel || {}), ...observation } };
  },
  getRubric() { return "Evaluate negotiation on adaptation, opponent modeling, and outcome quality."; },
  maxSteps() { return ${maxSteps}; },
};

module.exports = { scenario };
`;
}
