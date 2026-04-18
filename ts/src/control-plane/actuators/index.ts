// Public surface of the autocontext control-plane actuators layer.
// Importing this module registers all four concrete actuators as a side effect.
//
// Import discipline (§3.2): this module imports ONLY from contract/ (and its
// own subtree) — never from registry/, promotion/, or emit/.

// ---- Registry + interface types ----
export {
  registerActuator,
  getActuator,
  listActuatorTypes,
  __resetActuatorRegistryForTests,
} from "./registry.js";
export type { Actuator, ActuatorRegistration, WorkspaceLayoutArg } from "./registry.js";

// ---- Errors ----
export { CascadeRollbackRequired } from "./errors.js";

// ---- Shared helpers (exported for emit/ consumers) ----
export { emitUnifiedDiff } from "./_shared/unified-diff-emitter.js";
export type { EmitUnifiedDiffInputs } from "./_shared/unified-diff-emitter.js";
export { applySingleFile } from "./_shared/single-file-applicator.js";
export type { ApplySingleFileInputs } from "./_shared/single-file-applicator.js";
export { contentRevertRollback } from "./_shared/content-revert-rollback.js";
export type { ContentRevertInputs } from "./_shared/content-revert-rollback.js";

// ---- Concrete actuators (importing each registers it on the registry) ----
export {
  promptPatchActuator,
  promptPatchRegistration,
  PromptPatchPayloadSchema,
  PROMPT_PATCH_FILENAME,
} from "./prompt-patch/index.js";
export type { PromptPatchPayload } from "./prompt-patch/index.js";

export {
  toolPolicyActuator,
  toolPolicyRegistration,
  ToolPolicyPayloadSchema,
  ToolPolicyEntrySchema,
  TOOL_POLICY_FILENAME,
} from "./tool-policy/index.js";
export type { ToolPolicyPayload, ToolPolicyEntry } from "./tool-policy/index.js";

export {
  routingRuleActuator,
  routingRuleRegistration,
  RoutingRulePayloadSchema,
  RoutingRuleEntrySchema,
  ROUTING_RULE_FILENAME,
} from "./routing-rule/index.js";
export type { RoutingRulePayload, RoutingRuleEntry } from "./routing-rule/index.js";

export {
  fineTunedModelActuator,
  fineTunedModelRegistration,
  FineTunedModelPayloadSchema,
  FINE_TUNED_MODEL_FILENAME,
} from "./fine-tuned-model/index.js";
export type { FineTunedModelPayload } from "./fine-tuned-model/index.js";

export { importLegacyModelRecords } from "./fine-tuned-model/legacy-adapter.js";
