/**
 * Public barrel for the A2-I instrument/scanner module.
 */
export { scanRepo, type ScanOpts } from "./walker.js";
export { languageFromPath, isSupportedPath } from "./file-type-filter.js";
export {
  fromBytes,
  loadSourceFile,
  parseDirectives,
  parseDirectivesFromBytes,
  parseExistingImports,
  detectIndentationStyle,
} from "./source-file.js";
export {
  loadParser,
  parseSource,
  loadedGrammarsSnapshot,
  type LoadedParser,
  type TreeSitterTree,
} from "./tree-sitter-loader.js";
