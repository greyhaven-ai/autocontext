// Public surface of the autocontext control-plane eval-ingest layer.
// Import discipline (§3.2): imports contract/ and registry/ only.

export { attachEvalRun } from "./attach.js";
export type { AttachEvalRunResult } from "./attach.js";

export { validateEvalRunForIngestion } from "./validator.js";
export type { ValidateEvalRunForIngestionContext } from "./validator.js";

export { EvalRunAlreadyAttachedError } from "./errors.js";
