/**
 * Public barrel for the A2-I instrument/safety module.
 *
 * Decisions (Layer 3):
 *   - `gitignore-loader.ts` NOT extracted. Layer 1+2's inline implementation
 *     inside scanner/walker.ts couples the .gitignore cascade to the DFS
 *     walking state (per-directory `dirStack` accumulation + child-scope
 *     re-basing) in a way that does not cleanly factor without inventing a
 *     new stateful "walk context" abstraction. Since there is a single
 *     consumer (the walker) and the code is ~20 lines, extraction would add
 *     indirection without DRY payoff. If A2-II+ introduces a second consumer,
 *     revisit.
 */
export { HARDCODED_DEFAULT_PATTERNS } from "./hardcoded-defaults.js";
export {
  detectSecretLiterals,
  type SecretMatch,
} from "./secret-detector.js";
export { parseDirectives, parseDirectivesFromLines } from "./directive-parser.js";
