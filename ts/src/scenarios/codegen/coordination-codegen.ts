/**
 * Coordination family codegen (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/coordination_codegen.py.
 */

export function generateCoordinationSource(
  spec: Record<string, unknown>,
  name: string,
): string {
  const description = String(spec.description ?? "");
  const envDescription = String(spec.environment_description ?? spec.environmentDescription ?? "");
  const initialStateDescription = String(spec.initial_state_description ?? spec.initialStateDescription ?? "");
  const successCriteria = (spec.success_criteria ?? spec.successCriteria ?? []) as string[];
  const failureModes = (spec.failure_modes ?? spec.failureModes ?? []) as string[];
  const maxSteps = Number(spec.max_steps ?? spec.maxSteps ?? 30);
  const actions = (spec.actions ?? []) as Array<{
    name: string; description: string; parameters: Record<string, unknown>;
    preconditions: string[]; effects: string[];
  }>;
  const workers = (spec.workers ?? []) as Array<{
    id: string; role: string; partialContext: Record<string, unknown>;
  }>;

  return `// Generated coordination scenario: ${name}
const ACTIONS = ${JSON.stringify(actions, null, 2)};
const WORKERS = ${JSON.stringify(workers, null, 2)};

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
      timeline: [], terminal: false, handoffLog: [], mergedOutputs: [],
      workerOutputs: Object.fromEntries(WORKERS.map(w => [w.id, []])) };
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
  isTerminal(state) { return (state.step || 0) >= ${maxSteps}; },
  getResult(state, trace) {
    const records = trace?.records || [];
    const successes = records.filter(r => r.result?.success).length;
    const handoffs = (state.handoffLog || []).length;
    const merges = (state.mergedOutputs || []).length;
    const coordScore = WORKERS.length > 1 ? Math.min(1, (handoffs + merges) / (WORKERS.length * 2)) : 1;
    const successRate = records.length > 0 ? successes / records.length : 1;
    const score = Math.round((coordScore * 0.5 + successRate * 0.5) * 10000) / 10000;
    return { score, reasoning: handoffs + " handoffs, " + merges + " merges",
      dimensionScores: { coordination: Math.round(coordScore * 10000) / 10000, successRate: Math.round(successRate * 10000) / 10000 } };
  },
  getWorkerContexts() { return WORKERS.map(w => ({ ...w })); },
  getHandoffLog(state) { return state.handoffLog || []; },
  recordHandoff(state, fromWorker, toWorker, payload) {
    return { ...state, handoffLog: [...(state.handoffLog || []), { from: fromWorker, to: toWorker, payload, timestamp: Date.now() }] };
  },
  mergeOutputs(state, outputs) {
    const merged = Object.values(outputs).flat();
    return { ...state, mergedOutputs: [...(state.mergedOutputs || []), { outputs: merged, timestamp: Date.now() }] };
  },
  getRubric() { return "Evaluate handoff quality, merge correctness, and duplication avoidance."; },
  maxSteps() { return ${maxSteps}; },
};

module.exports = { scenario };
`;
}
