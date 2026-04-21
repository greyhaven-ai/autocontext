/**
 * A2-I safety — hardcoded-defaults skip-pattern list.
 *
 * Spec §5.1 step 1: the non-configurable, non-negotiable floor of paths the
 * scanner must skip. Appears as the first layer of the walker's filter chain
 * (before .gitignore, before --exclude, before the extension filter).
 *
 * Canonical home: `safety/hardcoded-defaults.ts`. The walker imports from here
 * (scanner → safety is allowed per spec §3.3 — safety is a pure, contract-free
 * constants module with no runtime dependencies).
 *
 * Spec-mandated pattern families (§5.1 item 1):
 *   .env*, .venv/**, node_modules/**, .git/**, .autocontext/**,
 *   *.pem, *.key, *.secret, *.p12, *.crt, *.cer
 *
 * The list below includes a couple of gitignore-dialect variants per family
 * (e.g., `.env` alongside `.env*`; `node_modules/` alongside `node_modules/**`)
 * so both the `ignore` npm package's directory-marker-based and glob-based
 * semantics produce the expected matches for common repo layouts.
 */

/**
 * Non-configurable skip-pattern list. Frozen so downstream code can't mutate
 * the safety floor.
 */
export const HARDCODED_DEFAULT_PATTERNS: readonly string[] = Object.freeze([
  // Environment files (any suffix: .env, .env.local, .env.production, …)
  ".env",
  ".env.*",
  ".env*",
  // Python virtualenvs
  ".venv/",
  ".venv/**",
  // Node package installs
  "node_modules/",
  "node_modules/**",
  // Git internal state
  ".git/",
  ".git/**",
  // Autocontext session directories (session dirs contain patches + plans we
  // produced; we must never re-scan them on subsequent invocations).
  ".autocontext/",
  ".autocontext/**",
  // Common key / cert filename suffixes (spec §5.1 item 1)
  "*.pem",
  "*.key",
  "*.secret",
  "*.p12",
  "*.crt",
  "*.cer",
]);
