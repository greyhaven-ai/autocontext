/**
 * Operator-loop family codegen — generates executable JS source (AC-432).
 *
 * Generates a scenario with a simulated operator that has configurable
 * escalation thresholds and judgment evaluation. The agent must decide
 * when to act autonomously vs escalate to the operator.
 */

export function generateOperatorLoopSource(
  spec: Record<string, unknown>,
  name: string,
): string {
  const description = String(spec.description ?? "");
  const envDescription = String(spec.environment_description ?? spec.environmentDescription ?? "");
  const initialStateDescription = String(spec.initial_state_description ?? spec.initialStateDescription ?? "");
  const successCriteria = (spec.success_criteria ?? spec.successCriteria ?? []) as string[];
  const failureModes = (spec.failure_modes ?? spec.failureModes ?? []) as string[];
  const maxSteps = Number(spec.max_steps ?? spec.maxSteps ?? 10);
  const actions = (spec.actions ?? []) as Array<{
    name: string; description: string; parameters: Record<string, unknown>;
    preconditions: string[]; effects: string[];
  }>;
  const escalationPolicy = (spec.escalation_policy ?? spec.escalationPolicy ?? {}) as Record<string, unknown>;

  return `// Generated operator_loop scenario: ${name}
const ACTIONS = ${JSON.stringify(actions, null, 2)};
const ESCALATION_POLICY = ${JSON.stringify(escalationPolicy)};

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
    return {
      seed: seed || 0,
      step: 0,
      completedActions: [],
      failedActions: [],
      timeline: [],
      terminal: false,
      escalationLog: [],
      clarificationLog: [],
      autonomousActions: 0,
      escalationPolicy: ESCALATION_POLICY,
      situationsRequiringEscalation: [],
    };
  },

  getAvailableActions(state) {
    const completed = new Set(state.completedActions || []);
    return ACTIONS.filter(a => !completed.has(a.name));
  },

  executeAction(state, action) {
    const nextState = {
      ...state,
      step: (state.step || 0) + 1,
      timeline: [...(state.timeline || [])],
      completedActions: [...(state.completedActions || [])],
      failedActions: [...(state.failedActions || [])],
      situationsRequiringEscalation: [...(state.situationsRequiringEscalation || [])],
    };

    const spec = ACTIONS.find(a => a.name === action.name);
    if (!spec) {
      nextState.failedActions.push(action.name);
      nextState.situationsRequiringEscalation.push({
        step: nextState.step, action: action.name, reason: "unknown action",
      });
      return {
        result: { success: false, output: "", stateChanges: {}, error: "unknown action: " + action.name },
        state: nextState,
      };
    }

    const completed = new Set(state.completedActions || []);
    for (const req of spec.preconditions || []) {
      if (!completed.has(req)) {
        nextState.failedActions.push(action.name);
        nextState.situationsRequiringEscalation.push({
          step: nextState.step, action: action.name, reason: "precondition: " + req,
        });
        return {
          result: { success: false, output: "", stateChanges: {}, error: "precondition not met: " + req },
          state: nextState,
        };
      }
    }

    nextState.completedActions.push(action.name);
    nextState.autonomousActions = (state.autonomousActions || 0) + 1;
    nextState.timeline.push({ action: action.name, parameters: action.parameters || {} });
    return {
      result: {
        success: true, output: "executed " + action.name,
        stateChanges: { completedActions: [...nextState.completedActions] },
        sideEffects: [action.name],
      },
      state: nextState,
    };
  },

  isTerminal(state) {
    const required = new Set(${JSON.stringify(actions.map(a => a.name))});
    const completed = new Set(state.completedActions || []);
    const allDone = [...required].every(r => completed.has(r));
    const maxEsc = (state.escalationPolicy || {}).maxEscalations || ${Number(escalationPolicy.max_escalations ?? 5)};
    const tooManyEscalations = (state.escalationLog || []).length > maxEsc;
    return allDone || (state.step || 0) >= ${maxSteps} || tooManyEscalations;
  },

  getResult(state, trace) {
    const judgment = scenario.evaluateJudgment(state);
    return {
      score: judgment.score,
      reasoning: judgment.reasoning,
      dimensionScores: judgment.dimensionScores,
    };
  },

  getEscalationLog(state) {
    return (state.escalationLog || []).map(e => ({ ...e }));
  },

  getClarificationLog(state) {
    return (state.clarificationLog || []).map(c => ({ ...c }));
  },

  escalate(state, event) {
    const nextState = {
      ...state,
      step: (state.step || 0) + 1,
      escalationLog: [...(state.escalationLog || []), event],
      timeline: [...(state.timeline || []), {
        type: "escalation", reason: event.reason,
        severity: event.severity, wasNecessary: event.wasNecessary,
      }],
    };
    return nextState;
  },

  requestClarification(state, request) {
    const nextState = {
      ...state,
      step: (state.step || 0) + 1,
      clarificationLog: [...(state.clarificationLog || []), request],
      timeline: [...(state.timeline || []), {
        type: "clarification", question: request.question, urgency: request.urgency,
      }],
    };
    return nextState;
  },

  evaluateJudgment(state) {
    const escalationLog = state.escalationLog || [];
    const situations = state.situationsRequiringEscalation || [];
    const totalEscalations = escalationLog.length;
    const necessary = escalationLog.filter(e => e.wasNecessary).length;
    const unnecessary = totalEscalations - necessary;
    const missed = situations.length;

    const precision = totalEscalations > 0 ? necessary / totalEscalations : 1;
    const totalShouldEscalate = necessary + missed;
    const recall = totalShouldEscalate > 0 ? necessary / totalShouldEscalate : 1;
    const autonomous = state.autonomousActions || 0;
    const totalActions = autonomous + totalEscalations;
    const autonomy = totalActions > 0 ? autonomous / totalActions : 0.5;
    const clarifications = (state.clarificationLog || []).length;
    const clarScore = clarifications > 0 ? Math.min(1.0, clarifications * 0.3) : 0.5;

    const score = Math.round((precision * 0.3 + recall * 0.3 + autonomy * 0.2 + clarScore * 0.2) * 10000) / 10000;

    return {
      score,
      reasoning: "Escalations: " + totalEscalations + " (" + necessary + " necessary, " + unnecessary + " unnecessary). " +
        "Missed: " + missed + ". Autonomous: " + autonomous + ". Clarifications: " + clarifications + ".",
      dimensionScores: {
        escalationPrecision: Math.round(precision * 10000) / 10000,
        escalationRecall: Math.round(recall * 10000) / 10000,
        autonomyEfficiency: Math.round(autonomy * 10000) / 10000,
        clarificationQuality: Math.round(clarScore * 10000) / 10000,
      },
      totalActions,
      escalations: totalEscalations,
      necessaryEscalations: necessary,
      unnecessaryEscalations: unnecessary,
      missedEscalations: missed,
      clarificationsRequested: clarifications,
    };
  },

  getRubric() {
    return "Evaluate escalation judgment: correct escalations (precision), caught necessary escalations (recall), appropriate autonomy, and clarification quality.";
  },

  maxSteps() { return ${maxSteps}; },
};

module.exports = { scenario };
`;
}
