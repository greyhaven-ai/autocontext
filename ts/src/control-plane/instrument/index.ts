/**
 * Public barrel for A2-I `autoctx instrument` tool infrastructure.
 *
 * Layers 1 + 2 + 3 + 4 + 5 + 6 + 7 — contract + scanner + safety + registry +
 * planner + pipeline + cli. (Layer 8 — LLM enhancer — lands next; its hooks
 * are wired as no-ops in pipeline/pr-body-renderer.ts with TODO markers.)
 *
 * Name-collision resolution:
 *   - `parseDirectives` is exported from BOTH `safety/` (canonical Buffer form)
 *     and `scanner/` (lines form, back-compat shim). The barrel re-exports the
 *     Buffer form as the public name `parseDirectives`, and the lines form as
 *     `parseDirectivesFromLines`. Downstream callers pick whichever shape fits.
 */
export type {
  InstrumentLanguage,
  DirectiveMap,
  DirectiveValue,
  IndentationStyle,
  ExistingImport,
  ImportSet,
  SourceRange,
  ImportSpec,
  BaseEdit,
  WrapExpressionEdit,
  InsertStatementEdit,
  ReplaceExpressionEdit,
  EditDescriptor,
  SecretMatch,
  SourceFile,
  DetectorPlugin,
  TreeSitterMatch,
  InstrumentSession,
  InstrumentFlagsSnapshot,
  PlanSourceFileMetadata,
  ConflictDecision,
  SafetyDecision,
  InstrumentPlan,
  ValidationResult,
} from "./contract/index.js";
export {
  validateInstrumentSession,
  validateInstrumentPlan,
} from "./contract/index.js";
// Scanner barrel minus the name-colliding `parseDirectives` (the lines form
// remains accessible via scanner/ internals; external callers get the Buffer
// form from safety/).
export {
  scanRepo,
  type ScanOpts,
  languageFromPath,
  isSupportedPath,
  fromBytes,
  loadSourceFile,
  parseDirectivesFromBytes,
  parseExistingImports,
  detectIndentationStyle,
  loadParser,
  parseSource,
  loadedGrammarsSnapshot,
  type LoadedParser,
  type TreeSitterTree,
} from "./scanner/index.js";
export {
  HARDCODED_DEFAULT_PATTERNS,
  detectSecretLiterals,
  type SecretMatch as DetectedSecretMatch,
  parseDirectives,
  parseDirectivesFromLines,
} from "./safety/index.js";
export {
  registerDetectorPlugin,
  pluginsForLanguage,
  resetRegistryForTests,
} from "./registry/index.js";
export {
  detectConflicts,
  type ConflictReport,
  type ConflictReason,
  planImports,
  type ImportPlan,
  type PlanImportsOpts,
  matchIndentation,
  type MatchIndentationOpts,
  composeEdits,
  type ComposeResult,
  type ComposedEdit,
  type RefusalReason,
  type ComposeEditsOpts,
} from "./planner/index.js";
export {
  runInstrument,
  type InstrumentInputs,
  type InstrumentResult,
  type InstrumentMode,
  type ConflictReason as PipelineConflictReason,
  checkCwdReadable,
  checkExcludeFromReadable,
  checkRegistryPopulated,
  checkWorkingTreeClean,
  checkBranchPreconditions,
  defaultGitDetector,
  type PreflightVerdict,
  type GitDetector,
  runDryRunMode,
  type DryRunModeInputs,
  type DetectionLine,
  runApplyMode,
  writeApplyLog,
  type ApplyModeInputs,
  type ApplyModeResult,
  runBranchMode,
  defaultBranchGitExecutor,
  type BranchModeInputs,
  type BranchModeResult,
  type BranchGitExecutor,
  renderPrBody,
  sha256ContentHash,
  type PrBodyInputs,
  type PerFileDetailedEdits,
  type SkippedFile,
  type DetectedUnchanged,
} from "./pipeline/index.js";
export {
  runInstrumentCommand,
  INSTRUMENT_HELP_TEXT,
  type CliResult as InstrumentCliResult,
  type RunnerOpts as InstrumentRunnerOpts,
} from "./cli/index.js";
