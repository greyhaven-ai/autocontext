import {
  hasSimulationMethodVariants,
  type MethodVariant,
} from "./family-contract-helpers.js";

export const NEGOTIATION_METHOD_VARIANTS: MethodVariant[] = [
  ["getHiddenPreferences", "get_hidden_preferences"],
  ["getRounds", "get_rounds"],
  ["getOpponentModel", "get_opponent_model"],
  ["updateOpponentModel", "update_opponent_model"],
  ["evaluateNegotiation", "evaluate_negotiation"],
];

export const INVESTIGATION_METHOD_VARIANTS: MethodVariant[] = [
  ["getEvidencePool", "get_evidence_pool"],
  ["evaluateEvidenceChain", "evaluate_evidence_chain"],
  ["evaluateDiagnosis", "evaluate_diagnosis"],
];

export const WORKFLOW_METHOD_VARIANTS: MethodVariant[] = [
  ["getWorkflowSteps", "get_workflow_steps"],
  ["executeStep", "execute_step"],
  ["executeCompensation", "execute_compensation"],
  ["getSideEffects", "get_side_effects"],
  ["evaluateWorkflow", "evaluate_workflow"],
];

export const SCHEMA_EVOLUTION_METHOD_VARIANTS: MethodVariant[] = [
  ["getMutations", "get_mutations"],
  ["getSchemaVersion", "get_schema_version"],
  ["getMutationLog", "get_mutation_log"],
  ["applyMutation", "apply_mutation"],
  ["checkContextValidity", "check_context_validity"],
  ["evaluateAdaptation", "evaluate_adaptation"],
];

export const TOOL_FRAGILITY_METHOD_VARIANTS: MethodVariant[] = [
  ["getToolContracts", "get_tool_contracts"],
  ["getDriftLog", "get_drift_log"],
  ["injectDrift", "inject_drift"],
  ["attributeFailure", "attribute_failure"],
  ["evaluateFragility", "evaluate_fragility"],
];

export const OPERATOR_LOOP_METHOD_VARIANTS: MethodVariant[] = [
  ["getEscalationLog", "get_escalation_log"],
  ["getClarificationLog", "get_clarification_log"],
  "escalate",
  ["requestClarification", "request_clarification"],
  ["evaluateJudgment", "evaluate_judgment"],
];

export const COORDINATION_METHOD_VARIANTS: MethodVariant[] = [
  ["getWorkerContexts", "get_worker_contexts"],
  ["getHandoffLog", "get_handoff_log"],
  ["recordHandoff", "record_handoff"],
  ["mergeOutputs", "merge_outputs"],
  ["evaluateCoordination", "evaluate_coordination"],
];

export function matchesSimulationFamilyContract(
  obj: unknown,
  methodVariants: readonly MethodVariant[],
): boolean {
  return hasSimulationMethodVariants(obj, ...methodVariants);
}
