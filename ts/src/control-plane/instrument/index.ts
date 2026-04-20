/**
 * Public barrel for A2-I `autoctx instrument` tool infrastructure.
 *
 * Layers 1 + 2 + 3 + 4 — contract + scanner + safety + registry. Additional
 * layers (planner, pipeline, llm, cli) land in follow-up commits per spec §11.6.
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
