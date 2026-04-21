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
export * from "./contract/index.js";
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
export * from "./safety/index.js";
export * from "./registry/index.js";
export * from "./planner/index.js";
export * from "./pipeline/index.js";
export {
  runInstrumentCommand,
  INSTRUMENT_HELP_TEXT,
  type CliResult as InstrumentCliResult,
  type RunnerOpts as InstrumentRunnerOpts,
} from "./cli/index.js";
