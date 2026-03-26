/**
 * Tool-fragility family codegen (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/tool_fragility_codegen.py.
 */

export function generateToolFragilitySource(
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
  const toolContracts = (spec.tool_contracts ?? spec.toolContracts ?? []) as Array<{
    toolName: string; expectedBehavior: string; driftBehavior: string;
  }>;

  return `// Generated tool_fragility scenario: ${name}
const ACTIONS = ${JSON.stringify(actions, null, 2)};
const TOOL_CONTRACTS = ${JSON.stringify(toolContracts, null, 2)};

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
      timeline: [], terminal: false, driftLog: [], driftInjected: false, attributions: [] };
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
    // If drift has been injected, affected tools may fail
    if (state.driftInjected) {
      const affected = TOOL_CONTRACTS.find(c => c.toolName === action.name);
      if (affected) {
        nextState.failedActions.push(action.name);
        return { result: { success: false, output: affected.driftBehavior, stateChanges: {}, error: "tool drift" }, state: nextState };
      }
    }
    nextState.completedActions.push(action.name);
    nextState.timeline.push({ action: action.name, parameters: action.parameters || {} });
    return { result: { success: true, output: "executed " + action.name, stateChanges: {} }, state: nextState };
  },
  isTerminal(state) { return (state.step || 0) >= ${maxSteps}; },
  getResult(state, trace) {
    const records = trace?.records || [];
    const successes = records.filter(r => r.result?.success).length;
    const driftDetected = (state.attributions || []).length > 0;
    const adaptations = (state.completedActions || []).length;
    const detectionScore = driftDetected ? 1 : 0;
    const adaptScore = ACTIONS.length > 0 ? Math.min(1, adaptations / ACTIONS.length) : 1;
    const score = Math.round((detectionScore * 0.4 + adaptScore * 0.4 + (successes / Math.max(records.length, 1)) * 0.2) * 10000) / 10000;
    return { score, reasoning: "Drift " + (driftDetected ? "detected" : "undetected") + ", " + adaptations + " adaptations",
      dimensionScores: { driftDetection: detectionScore, adaptation: Math.round(adaptScore * 10000) / 10000 } };
  },
  getToolContracts() { return TOOL_CONTRACTS.map(c => ({ ...c })); },
  getDriftLog(state) { return state.driftLog || []; },
  injectDrift(state, toolName) {
    return { ...state, driftInjected: true, driftLog: [...(state.driftLog || []), { toolName, timestamp: Date.now() }] };
  },
  attributeFailure(state, toolName, reason) {
    return { ...state, attributions: [...(state.attributions || []), { toolName, reason }] };
  },
  getRubric() { return "Evaluate drift detection accuracy, failure attribution, and adaptation quality."; },
  maxSteps() { return ${maxSteps}; },
};

module.exports = { scenario };
`;
}
