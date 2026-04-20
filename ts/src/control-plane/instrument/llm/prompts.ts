/**
 * A2-I Layer 8 — LLM enhancement prompt templates (spec §10.4).
 *
 * Prompts are TS constants with type-checked placeholder interpolation — no
 * markdown files, no runtime file I/O. Type-checked at build time; single
 * bundle. Customer-forkable prompt templates are reserved for a future
 * `--prompt-template-dir` override (spec §2 deferred items) but NOT shipped
 * in A2-I.
 *
 * All three sites described in spec §10.1:
 *   1. Per-call-site rationale (italic line under each before/after snippet)
 *   2. Per-file opt-out tip (hint box when a file looks unusual)
 *   3. Session summary (top-of-`pr-body.md` paragraph)
 */

export interface RationaleContext {
  readonly filePath: string;
  readonly language: "python" | "typescript" | "javascript" | "jsx" | "tsx";
  readonly sdkName: string;
  readonly beforeSnippet: string;
  readonly afterSnippet: string;
}

export interface FileOptOutTipContext {
  readonly filePath: string;
  readonly language: string;
  /** Heuristic signals detected about this file (e.g. "looks-like-test-file"). */
  readonly heuristicSignals: readonly string[];
}

export interface SessionSummaryContext {
  readonly filesAffected: number;
  readonly callSitesWrapped: number;
  readonly filesSkipped: number;
  readonly skippedBySecretLiteral: number;
  readonly registeredPluginIds: readonly string[];
}

/**
 * Rationale prompt (spec §10.1 site 1).
 *
 * Asks for 2-3 sentences explaining what the code change does and why.
 * Keeps temperature/model decisions to the provider layer.
 */
export function RATIONALE_PROMPT(ctx: RationaleContext): string {
  return [
    "You are describing a code change to a developer reviewing a pull request.",
    `File: ${ctx.filePath}`,
    `Language: ${ctx.language}`,
    `SDK detected: ${ctx.sdkName}`,
    "",
    "Before:",
    "```" + ctx.language,
    ctx.beforeSnippet,
    "```",
    "",
    "After:",
    "```" + ctx.language,
    ctx.afterSnippet,
    "```",
    "",
    "Write 2-3 sentences explaining what this change does and why it matters.",
    "Be concrete. Reference the wrapped client and what downstream emission it enables.",
    "Do not restate the diff. Do not add markdown headings or bullet points.",
    "Output only the prose — no preamble, no closing remark.",
  ].join("\n");
}

/**
 * Per-file opt-out tip prompt (spec §10.1 site 2).
 *
 * Suggests an opt-out path when a file looks unusual (test file not in
 * excludes, synthetic-traffic generator, etc). Output is a single short
 * hint (one or two sentences) that will surface in a dedicated hint box
 * in `pr-body.md`.
 */
export function FILE_OPT_OUT_TIP_PROMPT(ctx: FileOptOutTipContext): string {
  const signals = ctx.heuristicSignals.length
    ? ctx.heuristicSignals.join(", ")
    : "none";
  return [
    "You are a helpful coding assistant reviewing an instrumentation plan.",
    `File: ${ctx.filePath}`,
    `Language: ${ctx.language}`,
    `Heuristic signals: ${signals}`,
    "",
    "This file looks unusual for instrumentation. Write a single short hint",
    "(one or two sentences) suggesting how to opt out if that wasn't intended.",
    "Mention both path-level (`.gitignore` or `--exclude`) and file-level",
    "(`# autocontext: off-file`) approaches. Keep it actionable and terse.",
    "Output only the hint prose — no preamble, no markdown headings.",
  ].join("\n");
}

/**
 * Session summary prompt (spec §10.1 site 3).
 *
 * Asks for a one-paragraph overview for the top of `pr-body.md`. Highlights
 * anything notable (e.g., "two files were skipped due to secret literals").
 */
export function SESSION_SUMMARY_PROMPT(ctx: SessionSummaryContext): string {
  const plugins = ctx.registeredPluginIds.length
    ? ctx.registeredPluginIds.join(", ")
    : "(none)";
  return [
    "You are writing a one-paragraph summary of an autocontext instrumentation session.",
    `Files affected: ${ctx.filesAffected}`,
    `Call sites wrapped: ${ctx.callSitesWrapped}`,
    `Files skipped: ${ctx.filesSkipped}`,
    `Files skipped due to secret-literal detection: ${ctx.skippedBySecretLiteral}`,
    `Registered detector plugins: ${plugins}`,
    "",
    "Write one paragraph (3-5 sentences) that orients a reviewer to this session.",
    "Highlight anything notable — especially safety-related skips. Do not restate",
    "every number; pick what matters. Do not add markdown headings or bullet points.",
    "Output only the prose.",
  ].join("\n");
}
