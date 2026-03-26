/**
 * Investigation family codegen (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/investigation_codegen.py.
 */

export function generateInvestigationSource(
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
  const evidencePool = (spec.evidence_pool ?? spec.evidencePool ?? []) as Array<{
    id: string; content: string; isRedHerring?: boolean; relevance: number;
  }>;
  const correctDiagnosis = String(spec.correct_diagnosis ?? spec.correctDiagnosis ?? "");

  const actionsJson = JSON.stringify(actions, null, 2);
  const evidenceJson = JSON.stringify(evidencePool, null, 2);

  return `// Generated investigation scenario: ${name}
const ACTIONS = ${actionsJson};
const EVIDENCE_POOL = ${evidenceJson};
const CORRECT_DIAGNOSIS = ${JSON.stringify(correctDiagnosis)};

const scenario = {
  name: ${JSON.stringify(name)},

  describeScenario() { return ${JSON.stringify(description)}; },

  describeEnvironment() {
    return {
      name: ${JSON.stringify(name)},
      description: ${JSON.stringify(envDescription)},
      availableActions: ACTIONS,
      initialStateDescription: ${JSON.stringify(initialStateDescription)},
      successCriteria: ${JSON.stringify(successCriteria)},
      failureModes: ${JSON.stringify(failureModes)},
    };
  },

  initialState(seed) {
    return { seed: seed || 0, step: 0, completedActions: [], failedActions: [],
             timeline: [], terminal: false, collectedEvidence: [], diagnosis: null };
  },

  getAvailableActions(state) {
    const completed = new Set(state.completedActions || []);
    return ACTIONS.filter(a => !completed.has(a.name));
  },

  executeAction(state, action) {
    const nextState = { ...state, step: (state.step || 0) + 1,
      timeline: [...(state.timeline || [])], completedActions: [...(state.completedActions || [])],
      failedActions: [...(state.failedActions || [])], collectedEvidence: [...(state.collectedEvidence || [])] };
    const spec = ACTIONS.find(a => a.name === action.name);
    if (!spec) {
      nextState.failedActions.push(action.name);
      return { result: { success: false, output: "", stateChanges: {}, error: "unknown action" }, state: nextState };
    }
    nextState.completedActions.push(action.name);
    nextState.timeline.push({ action: action.name, parameters: action.parameters || {} });
    // Collecting evidence through actions
    const relatedEvidence = EVIDENCE_POOL.filter(e => action.name.toLowerCase().includes(e.id.toLowerCase().split("_")[0]));
    for (const e of relatedEvidence) {
      if (!nextState.collectedEvidence.find(ce => ce.id === e.id)) {
        nextState.collectedEvidence.push(e);
      }
    }
    return { result: { success: true, output: "executed " + action.name, stateChanges: {}, sideEffects: [action.name] }, state: nextState };
  },

  isTerminal(state) {
    return state.diagnosis != null || (state.step || 0) >= ${maxSteps};
  },

  getResult(state, trace) {
    const records = trace?.records || [];
    const successes = records.filter(r => r.result?.success).length;
    const collected = (state.collectedEvidence || []).filter(e => !e.isRedHerring);
    const realEvidence = EVIDENCE_POOL.filter(e => !e.isRedHerring);
    const evidenceCoverage = realEvidence.length > 0 ? collected.length / realEvidence.length : 1;
    const diagnosisCorrect = state.diagnosis && state.diagnosis.toLowerCase().includes(CORRECT_DIAGNOSIS.toLowerCase()) ? 1 : 0;
    const score = Math.round((evidenceCoverage * 0.4 + diagnosisCorrect * 0.4 + (successes / Math.max(records.length, 1)) * 0.2) * 10000) / 10000;
    return {
      score, reasoning: "Evidence coverage: " + Math.round(evidenceCoverage * 100) + "%, diagnosis " + (diagnosisCorrect ? "correct" : "incorrect"),
      dimensionScores: { evidenceCoverage: Math.round(evidenceCoverage * 10000) / 10000, diagnosisAccuracy: diagnosisCorrect, efficiency: Math.round((successes / Math.max(records.length, 1)) * 10000) / 10000 },
    };
  },

  getEvidencePool() { return EVIDENCE_POOL.map(e => ({ ...e })); },

  evaluateEvidenceChain(chain) {
    const realIds = new Set(EVIDENCE_POOL.filter(e => !e.isRedHerring).map(e => e.id));
    const chainIds = (chain || []).map(e => e.id);
    const correct = chainIds.filter(id => realIds.has(id)).length;
    const redHerringIncluded = chainIds.filter(id => !realIds.has(id)).length;
    return { score: correct / Math.max(realIds.size, 1), correct, total: chainIds.length, redHerrings: redHerringIncluded };
  },

  evaluateDiagnosis(diagnosis) {
    const correct = diagnosis && diagnosis.toLowerCase().includes(CORRECT_DIAGNOSIS.toLowerCase());
    return { correct: !!correct, score: correct ? 1.0 : 0.0, expected: CORRECT_DIAGNOSIS };
  },

  getRubric() { return "Evaluate evidence gathering, red herring avoidance, and diagnosis accuracy."; },
  maxSteps() { return ${maxSteps}; },
};

module.exports = { scenario };
`;
}
