/**
 * Schema-evolution family codegen (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/schema_evolution_codegen.py.
 */

export function generateSchemaEvolutionSource(
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
  const mutations = (spec.mutations ?? []) as Array<{
    version: number; description: string; changes: Record<string, unknown>;
  }>;

  return `// Generated schema_evolution scenario: ${name}
const ACTIONS = ${JSON.stringify(actions, null, 2)};
const MUTATIONS = ${JSON.stringify(mutations, null, 2)};

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
      timeline: [], terminal: false, schemaVersion: 0, mutationLog: [], staleDetections: 0 };
  },
  getAvailableActions(state) {
    const completed = new Set(state.completedActions || []);
    return ACTIONS.filter(a => !completed.has(a.name));
  },
  executeAction(state, action) {
    const nextState = { ...state, step: (state.step || 0) + 1,
      timeline: [...(state.timeline || [])], completedActions: [...(state.completedActions || [])],
      failedActions: [...(state.failedActions || [])] };
    const spec = ACTIONS.find(a => a.name === action.name);
    if (!spec) {
      nextState.failedActions.push(action.name);
      return { result: { success: false, output: "", stateChanges: {}, error: "unknown action" }, state: nextState };
    }
    nextState.completedActions.push(action.name);
    nextState.timeline.push({ action: action.name, parameters: action.parameters || {} });
    return { result: { success: true, output: "executed " + action.name, stateChanges: {} }, state: nextState };
  },
  isTerminal(state) { return (state.schemaVersion || 0) >= MUTATIONS.length || (state.step || 0) >= ${maxSteps}; },
  getResult(state, trace) {
    const versionsHandled = state.schemaVersion || 0;
    const coverage = MUTATIONS.length > 0 ? versionsHandled / MUTATIONS.length : 1;
    const detections = state.staleDetections || 0;
    const detectionRate = MUTATIONS.length > 0 ? Math.min(1, detections / MUTATIONS.length) : 1;
    const score = Math.round((coverage * 0.5 + detectionRate * 0.5) * 10000) / 10000;
    return { score, reasoning: versionsHandled + "/" + MUTATIONS.length + " versions handled",
      dimensionScores: { schemaCoverage: Math.round(coverage * 10000) / 10000, staleDetection: Math.round(detectionRate * 10000) / 10000 } };
  },
  getMutations() { return MUTATIONS.map(m => ({ ...m })); },
  getSchemaVersion(state) { return state.schemaVersion || 0; },
  getMutationLog(state) { return state.mutationLog || []; },
  applyMutation(state, mutation) {
    const nextState = { ...state, schemaVersion: (state.schemaVersion || 0) + 1,
      mutationLog: [...(state.mutationLog || []), mutation], staleDetections: (state.staleDetections || 0) + 1 };
    return nextState;
  },
  getRubric() { return "Evaluate schema migration handling, stale context detection, and adaptation quality."; },
  maxSteps() { return ${maxSteps}; },
};

module.exports = { scenario };
`;
}
