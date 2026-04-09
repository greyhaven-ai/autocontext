export const INVESTIGATION_SCENARIO_TEMPLATE = String.raw`// Generated investigation scenario: __SCENARIO_NAME_COMMENT__
const ACTIONS = __ACTIONS__;
const EVIDENCE_POOL = __EVIDENCE_POOL__;
const CORRECT_DIAGNOSIS = __CORRECT_DIAGNOSIS__;

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
      collectedEvidence: [],
      diagnosis: null,
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
      collectedEvidence: [...(state.collectedEvidence || [])],
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
    const relatedEvidence = EVIDENCE_POOL.filter((evidence) => action.name.toLowerCase().includes(evidence.id.toLowerCase().split("_")[0]));
    for (const evidence of relatedEvidence) {
      if (!nextState.collectedEvidence.find((collected) => collected.id === evidence.id)) {
        nextState.collectedEvidence.push(evidence);
      }
    }
    return {
      result: {
        success: true,
        output: "executed " + action.name,
        stateChanges: {},
        sideEffects: [action.name],
      },
      state: nextState,
    };
  },

  isTerminal(state) {
    return state.diagnosis != null || (state.step || 0) >= __MAX_STEPS__;
  },

  getResult(state, trace) {
    const records = trace?.records || [];
    const successes = records.filter((record) => record.result?.success).length;
    const collected = (state.collectedEvidence || []).filter((evidence) => !evidence.isRedHerring);
    const realEvidence = EVIDENCE_POOL.filter((evidence) => !evidence.isRedHerring);
    const evidenceCoverage = realEvidence.length > 0 ? collected.length / realEvidence.length : 1;
    const diagnosisCorrect = state.diagnosis && state.diagnosis.toLowerCase().includes(CORRECT_DIAGNOSIS.toLowerCase()) ? 1 : 0;
    const score = Math.round((evidenceCoverage * 0.4 + diagnosisCorrect * 0.4 + (successes / Math.max(records.length, 1)) * 0.2) * 10000) / 10000;
    return {
      score,
      reasoning: "Evidence coverage: " + Math.round(evidenceCoverage * 100) + "% , diagnosis " + (diagnosisCorrect ? "correct" : "incorrect"),
      dimensionScores: {
        evidenceCoverage: Math.round(evidenceCoverage * 10000) / 10000,
        diagnosisAccuracy: diagnosisCorrect,
        efficiency: Math.round((successes / Math.max(records.length, 1)) * 10000) / 10000,
      },
    };
  },

  getEvidencePool() {
    return EVIDENCE_POOL.map((evidence) => ({ ...evidence }));
  },

  evaluateEvidenceChain(chain) {
    const realIds = new Set(EVIDENCE_POOL.filter((evidence) => !evidence.isRedHerring).map((evidence) => evidence.id));
    const chainIds = (chain || []).map((evidence) => evidence.id);
    const correct = chainIds.filter((id) => realIds.has(id)).length;
    const redHerringIncluded = chainIds.filter((id) => !realIds.has(id)).length;
    return { score: correct / Math.max(realIds.size, 1), correct, total: chainIds.length, redHerrings: redHerringIncluded };
  },

  evaluateDiagnosis(diagnosis) {
    const correct = diagnosis && diagnosis.toLowerCase().includes(CORRECT_DIAGNOSIS.toLowerCase());
    return { correct: !!correct, score: correct ? 1.0 : 0.0, expected: CORRECT_DIAGNOSIS };
  },

  getRubric() {
    return "Evaluate evidence gathering, red herring avoidance, and diagnosis accuracy.";
  },

  maxSteps() {
    return __MAX_STEPS__;
  },
};

module.exports = { scenario };
`;
