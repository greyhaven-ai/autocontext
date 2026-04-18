// Public surface of the autocontext control-plane emit layer.
// Import discipline (§3.2): emit/ imports contract/, registry/, promotion/,
// actuators/ — never the reverse. No module side effects.

export { emitPr, EmitPreflightError } from "./pipeline.js";
export type {
  EmitMode,
  EmitPrOptions,
  EmitResult,
  EmitLocation,
  EmitLocationPrUrl,
  EmitLocationBranch,
  EmitLocationLocalPath,
} from "./pipeline.js";

export { branchNameFor } from "./branch-namer.js";
export { renderPatches } from "./patch-renderer.js";
export type { RenderPatchesInputs } from "./patch-renderer.js";
export { renderPrBody } from "./pr-body-renderer.js";
export type { RenderPrBodyInputs } from "./pr-body-renderer.js";
export { preflight } from "./preflight.js";
export type {
  PreflightMode,
  PreflightIssue,
  PreflightResult,
  PreflightDetector,
  PreflightInputs,
} from "./preflight.js";
export { resolveAutoMode } from "./modes/auto.js";
export type { ResolvedMode, AutoDetector } from "./modes/auto.js";
export { runPatchOnlyMode } from "./modes/patch-only.js";
export { runGitMode } from "./modes/git.js";
export { runGhMode } from "./modes/gh.js";
export {
  defaultWorkspaceLayout,
  loadWorkspaceLayout,
} from "./workspace-layout.js";
export type { WorkspaceLayout } from "./workspace-layout.js";
