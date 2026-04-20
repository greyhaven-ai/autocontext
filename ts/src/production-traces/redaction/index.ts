// Public surface for the production-traces redaction sub-context.
//
// See spec §7 for the policy and semantic contract, and the `traces/redaction-*`
// modules for the underlying text-pattern primitives we wrap.

export type {
  LoadedRedactionPolicy,
  RedactionMode,
  CategoryAction,
  CategoryOverride,
  CustomPolicyPattern,
  ExportPolicy,
  RawProviderPayloadBehavior,
} from "./types.js";

export {
  defaultRedactionPolicy,
  loadRedactionPolicy,
  saveRedactionPolicy,
  redactionPolicyPath,
} from "./policy.js";

export { markRedactions } from "./mark.js";

export { applyRedactions } from "./apply.js";

export {
  initializeInstallSalt,
  loadInstallSalt,
  rotateInstallSalt,
  installSaltPath,
} from "./install-salt.js";
