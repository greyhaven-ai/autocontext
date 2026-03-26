/**
 * Workflow family codegen (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/workflow_codegen.py.
 */

export function generateWorkflowSource(
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
  const steps = (spec.steps ?? spec.workflow_steps ?? []) as Array<{
    name: string; description: string; compensationAction?: string;
    sideEffects?: string[]; retryable?: boolean;
  }>;

  return `// Generated workflow scenario: ${name}
const ACTIONS = ${JSON.stringify(actions, null, 2)};
const WORKFLOW_STEPS = ${JSON.stringify(steps, null, 2)};

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
      timeline: [], terminal: false, completedSteps: [], compensations: [], sideEffects: [] };
  },
  getAvailableActions(state) {
    const completed = new Set(state.completedActions || []);
    return ACTIONS.filter(a => !completed.has(a.name));
  },
  executeAction(state, action) {
    const nextState = { ...state, step: (state.step || 0) + 1,
      timeline: [...(state.timeline || [])], completedActions: [...(state.completedActions || [])],
      failedActions: [...(state.failedActions || [])], sideEffects: [...(state.sideEffects || [])] };
    const spec = ACTIONS.find(a => a.name === action.name);
    if (!spec) {
      nextState.failedActions.push(action.name);
      return { result: { success: false, output: "", stateChanges: {}, error: "unknown action" }, state: nextState };
    }
    const completed = new Set(state.completedActions || []);
    for (const req of spec.preconditions || []) {
      if (!completed.has(req)) {
        nextState.failedActions.push(action.name);
        return { result: { success: false, output: "", stateChanges: {}, error: "precondition: " + req }, state: nextState };
      }
    }
    nextState.completedActions.push(action.name);
    nextState.timeline.push({ action: action.name, parameters: action.parameters || {} });
    const stepSpec = WORKFLOW_STEPS.find(s => s.name === action.name);
    if (stepSpec?.sideEffects) nextState.sideEffects.push(...stepSpec.sideEffects);
    return { result: { success: true, output: "executed " + action.name, stateChanges: {}, sideEffects: stepSpec?.sideEffects || [] }, state: nextState };
  },
  isTerminal(state) {
    const stepNames = new Set(WORKFLOW_STEPS.map(s => s.name));
    const completed = new Set(state.completedActions || []);
    return [...stepNames].every(s => completed.has(s)) || (state.step || 0) >= ${maxSteps};
  },
  getResult(state, trace) {
    const stepNames = new Set(WORKFLOW_STEPS.map(s => s.name));
    const completed = new Set(state.completedActions || []);
    const stepsCompleted = [...stepNames].filter(s => completed.has(s)).length;
    const completion = stepNames.size > 0 ? stepsCompleted / stepNames.size : 1;
    const records = trace?.records || [];
    const failures = records.filter(r => !r.result?.success).length;
    const recovery = failures === 0 ? 1 : Math.max(0.2, 1 - failures / Math.max(records.length, 1));
    const score = Math.round((completion * 0.5 + recovery * 0.3 + (records.length > 0 ? 0.2 : 0)) * 10000) / 10000;
    return { score, reasoning: stepsCompleted + "/" + stepNames.size + " steps, " + failures + " failures",
      dimensionScores: { completion: Math.round(completion * 10000) / 10000, recovery: Math.round(recovery * 10000) / 10000 } };
  },
  getWorkflowSteps() { return WORKFLOW_STEPS.map(s => ({ ...s })); },
  executeStep(state, stepName) {
    const step = WORKFLOW_STEPS.find(s => s.name === stepName);
    if (!step) return { success: false, error: "unknown step: " + stepName };
    return scenario.executeAction(state, { name: stepName, parameters: {} });
  },
  executeCompensation(state, stepName) {
    const step = WORKFLOW_STEPS.find(s => s.name === stepName);
    if (!step?.compensationAction) return { success: false, error: "no compensation for: " + stepName };
    const nextState = { ...state, compensations: [...(state.compensations || []), stepName] };
    return { result: { success: true, output: "compensated " + stepName }, state: nextState };
  },
  getSideEffects(state) { return state.sideEffects || []; },
  getRubric() { return "Evaluate on workflow completion, compensation correctness, and side-effect tracking."; },
  maxSteps() { return ${maxSteps}; },
};

module.exports = { scenario };
`;
}
