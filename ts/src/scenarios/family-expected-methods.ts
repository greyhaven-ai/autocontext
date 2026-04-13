import type { ScenarioFamilyName } from "./families.js";

const GAME_METHODS = [
  "describeRules",
  "initialState",
  "step",
  "isTerminal",
  "getResult",
  "executeMatch",
] as const;

const AGENT_TASK_METHODS = [
  "getTaskPrompt",
  "evaluateOutput",
  "getRubric",
  "initialState",
  "describeTask",
] as const;

const SIMULATION_METHODS = [
  "describeScenario",
  "describeEnvironment",
  "initialState",
  "getAvailableActions",
  "executeAction",
  "isTerminal",
  "evaluateTrace",
  "getRubric",
] as const;

const NEGOTIATION_METHODS = [
  ...SIMULATION_METHODS,
  "getHiddenPreferences",
  "getRounds",
  "getOpponentModel",
  "updateOpponentModel",
  "evaluateNegotiation",
] as const;

const INVESTIGATION_METHODS = [
  ...SIMULATION_METHODS,
  "getEvidencePool",
  "evaluateEvidenceChain",
  "evaluateDiagnosis",
] as const;

const WORKFLOW_METHODS = [
  ...SIMULATION_METHODS,
  "getWorkflowSteps",
  "executeStep",
  "executeCompensation",
  "getSideEffects",
  "evaluateWorkflow",
] as const;

const SCHEMA_EVOLUTION_METHODS = [
  ...SIMULATION_METHODS,
  "getMutations",
  "getSchemaVersion",
  "getMutationLog",
  "applyMutation",
  "checkContextValidity",
  "evaluateAdaptation",
] as const;

const TOOL_FRAGILITY_METHODS = [
  ...SIMULATION_METHODS,
  "getToolContracts",
  "getDriftLog",
  "injectDrift",
  "attributeFailure",
  "evaluateFragility",
] as const;

const OPERATOR_LOOP_METHODS = [
  ...SIMULATION_METHODS,
  "getEscalationLog",
  "getClarificationLog",
  "escalate",
  "requestClarification",
  "evaluateJudgment",
] as const;

const COORDINATION_METHODS = [
  ...SIMULATION_METHODS,
  "getWorkerContexts",
  "getHandoffLog",
  "recordHandoff",
  "mergeOutputs",
  "evaluateCoordination",
] as const;

const ARTIFACT_EDITING_METHODS = [
  "describeTask",
  "getRubric",
  "initialArtifacts",
  "getEditPrompt",
  "validateArtifact",
  "evaluateEdits",
] as const;

export const EXPECTED_METHODS: Record<ScenarioFamilyName, readonly string[]> = {
  game: GAME_METHODS,
  agent_task: AGENT_TASK_METHODS,
  simulation: SIMULATION_METHODS,
  negotiation: NEGOTIATION_METHODS,
  investigation: INVESTIGATION_METHODS,
  workflow: WORKFLOW_METHODS,
  schema_evolution: SCHEMA_EVOLUTION_METHODS,
  tool_fragility: TOOL_FRAGILITY_METHODS,
  operator_loop: OPERATOR_LOOP_METHODS,
  coordination: COORDINATION_METHODS,
  artifact_editing: ARTIFACT_EDITING_METHODS,
};
