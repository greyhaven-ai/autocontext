/**
 * A2-I Layer 5 — planner barrel.
 *
 * Re-exports the public API of the planner sub-context (spec §6). Internal
 * implementation details (e.g., AST helpers, private conflict heuristics) stay
 * module-local.
 *
 * Import discipline (spec §3.3):
 *   - planner/ imports from instrument/contract/ and control-plane/actuators/_shared/
 *   - planner/ NEVER imports from scanner/, safety/, registry/, or pipeline/
 *
 * Layer ordering: conflict-detector is the lowest primitive; import-manager and
 * indentation-matcher are peers; edit-composer orchestrates all three.
 */
export { detectConflicts } from "./conflict-detector.js";
export type { ConflictReport, ConflictReason } from "./conflict-detector.js";

export { planImports } from "./import-manager.js";
export type { ImportPlan, PlanImportsOpts } from "./import-manager.js";

export { matchIndentation } from "./indentation-matcher.js";
export type { MatchIndentationOpts } from "./indentation-matcher.js";

export { composeEdits } from "./edit-composer.js";
export type { ComposeResult, ComposedEdit, RefusalReason, ComposeEditsOpts } from "./edit-composer.js";
