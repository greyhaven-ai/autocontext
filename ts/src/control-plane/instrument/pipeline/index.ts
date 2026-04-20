/**
 * A2-I Layer 6 — pipeline barrel.
 *
 * Public surface of the pipeline sub-context (spec §7). `cli/` imports from
 * here; nobody else does.
 */
export { runInstrument } from "./orchestrator.js";
export type {
  InstrumentInputs,
  InstrumentResult,
  InstrumentMode,
  ConflictReason,
} from "./orchestrator.js";

export {
  checkCwdReadable,
  checkExcludeFromReadable,
  checkRegistryPopulated,
  checkWorkingTreeClean,
  checkBranchPreconditions,
  defaultGitDetector,
} from "./preflight.js";
export type { PreflightVerdict, GitDetector } from "./preflight.js";

export { runDryRunMode } from "./modes/dry-run.js";
export type { DryRunModeInputs, DetectionLine } from "./modes/dry-run.js";

export { runApplyMode, writeApplyLog } from "./modes/apply.js";
export type { ApplyModeInputs, ApplyModeResult } from "./modes/apply.js";

export { runBranchMode, defaultBranchGitExecutor } from "./modes/branch.js";
export type { BranchModeInputs, BranchModeResult, BranchGitExecutor } from "./modes/branch.js";

export { renderPrBody, sha256ContentHash } from "./pr-body-renderer.js";
export type {
  PrBodyInputs,
  PerFileDetailedEdits,
  SkippedFile,
  DetectedUnchanged,
} from "./pr-body-renderer.js";
