/**
 * A2-I Layer 7 — CLI runner.
 *
 * In-process dispatch for `autoctx instrument`. No `process.exit`, no
 * `console` from within the command handler — it returns
 * `{ stdout, stderr, exitCode }` and the outer adapter (ts/src/cli/index.ts)
 * prints + exits. Tests consume the runner directly for speed.
 *
 * Mirrors Foundation B's `runControlPlaneCommand` and Foundation A's
 * `runProductionTracesCommand` shape.
 */
import { ulid } from "ulid";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { runInstrument, type InstrumentInputs, type InstrumentMode, type InstrumentResult } from "../pipeline/orchestrator.js";
import { formatOutput, type OutputMode } from "../../cli/_shared/output-formatters.js";
import type { GitDetector } from "../pipeline/preflight.js";
import type { BranchGitExecutor } from "../pipeline/modes/branch.js";

export type CliResult = { readonly stdout: string; readonly stderr: string; readonly exitCode: number };

export interface RunnerOpts {
  /** Override current working directory for the instrument run (defaults to process.cwd()). */
  readonly cwd?: string;
  /** Injected clock for deterministic testing (defaults to new Date().toISOString()). */
  readonly nowIso?: string;
  /** Injected ULID for deterministic testing (defaults to a fresh `ulid()`). */
  readonly sessionUlid?: string;
  /** Autoctx version string to embed in session metadata. */
  readonly autoctxVersion?: string;
  /** Optional git detector injected for preflight + branch mode. */
  readonly gitDetector?: GitDetector;
  /** Optional branch-mode git executor (for apply-branch tests). */
  readonly branchExecutor?: BranchGitExecutor;
}

export const INSTRUMENT_HELP_TEXT = `autoctx instrument — scan a repo for LLM clients and propose/apply Autocontext wrappers

Usage:
  autoctx instrument [--dry-run | --apply [--branch <name>] [--commit <msg>]]
                     [--exclude <glob>]... [--exclude-from <file>]
                     [--enhanced] [--max-file-bytes <N>]
                     [--fail-if-empty] [--output json|table|pretty]
                     [--force]

Modes (mutually exclusive; default is --dry-run):
  --dry-run             Compose patches + session directory; do NOT mutate files.
  --apply               Write patches into the working tree (requires clean tree or --force).
  --apply --branch <n>  Branch off HEAD, apply, commit. (--commit <msg> optional).

Filters:
  --exclude <glob>      Repeatable. Adds gitignore-style exclude patterns.
  --exclude-from <file> Read additional excludes from <file> (gitignore syntax).
  --max-file-bytes <N>  Skip files over N bytes (default 1048576 = 1 MiB).

Safety / observability:
  --fail-if-empty       Exit 12 when zero DetectorPlugins are registered (A2-I default state).
  --force               Bypass clean-tree preflight. Print a prominent stderr warning.
  --enhanced            Force LLM enhancement of pr-body narrative. Without a provider wired
                        or without an API key, enhancement silently falls back to the
                        deterministic default templates — plan.json stays byte-identical
                        whether enhancement is on or off.

Output:
  --output json|table|pretty
                        Format for the summary printed to stdout (default: pretty).

Exit codes (spec §8.2):
  0   success (including "no detections" when --fail-if-empty not set)
  1   domain failure (plugin error; unresolvable imports)
  2   partial success (some files skipped — advisory)
  11  invalid --exclude-from / unparsable flags
  12  no files matched + --fail-if-empty set
  13  plugin conflict (overlapping edits; unresolvable)
  14  I/O failure
  15  dirty working tree (apply refused); --force overrides
  16  --apply --branch requested but no git repo / base branch missing
`;

/**
 * Auto-load `.autoctx.instrument.config.{mjs,js,ts}` from `cwd` if present.
 *
 * Priority order: `.mjs` > `.js` > `.ts` (first found wins, others ignored).
 * The file is dynamic-imported so ESM `import()` resolution applies. Any
 * `registerDetectorPlugin()` calls in the config module execute at import time,
 * populating the process-global registry before the scanner runs.
 *
 * Errors during import propagate to the caller (treated as exit 14).
 */
async function loadConfigFileIfPresent(cwd: string): Promise<void> {
  for (const name of [
    ".autoctx.instrument.config.mjs",
    ".autoctx.instrument.config.js",
    ".autoctx.instrument.config.ts",
  ]) {
    const p = join(cwd, name);
    if (existsSync(p)) {
      await import(pathToFileURL(p).href);
      return;
    }
  }
}

/**
 * Parse `argv` (the args AFTER `autoctx instrument`), dispatch to
 * `runInstrument`, format the result per `--output`.
 */
export async function runInstrumentCommand(
  argv: readonly string[],
  opts: RunnerOpts = {},
): Promise<CliResult> {
  if (argv[0] === "--help" || argv[0] === "-h") {
    return { stdout: INSTRUMENT_HELP_TEXT, stderr: "", exitCode: 0 };
  }

  const parsed = parseInstrumentFlags(argv);
  if ("error" in parsed) {
    return {
      stdout: "",
      stderr: `${parsed.error}\n${INSTRUMENT_HELP_TEXT}`,
      exitCode: parsed.exitCode ?? 11,
    };
  }
  const flags = parsed.value;

  if (flags.mode === "apply-branch" && !flags.branchName) {
    // Unreachable in practice (parseInstrumentFlags rejects before we get here)
    // but keeps the types honest.
    return {
      stdout: "",
      stderr: "--branch requires a value (e.g., --branch autocontext-instrument)",
      exitCode: 11,
    };
  }

  const cwd = opts.cwd ?? process.cwd();
  const nowIso = opts.nowIso ?? new Date().toISOString();
  const sessionUlid = opts.sessionUlid ?? ulid();

  // Auto-load config file before scanner runs so plugins are registered.
  try {
    await loadConfigFileIfPresent(cwd);
  } catch (err) {
    return {
      stdout: "",
      stderr: `config file load failed: ${err instanceof Error ? err.message : String(err)}`,
      exitCode: 14,
    };
  }

  const inputs: InstrumentInputs = {
    cwd,
    mode: flags.mode,
    nowIso,
    sessionUlid,
    ...(flags.branchName !== undefined ? { branchName: flags.branchName } : {}),
    ...(flags.commitMessage !== undefined ? { commitMessage: flags.commitMessage } : {}),
    ...(flags.excludes.length > 0 ? { exclude: flags.excludes } : {}),
    ...(flags.excludeFrom !== undefined ? { excludeFrom: flags.excludeFrom } : {}),
    ...(flags.maxFileBytes !== undefined ? { maxFileBytes: flags.maxFileBytes } : {}),
    ...(flags.failIfEmpty ? { failIfEmpty: true } : {}),
    ...(flags.force ? { force: true } : {}),
    ...(flags.enhanced ? { enhanced: true } : {}),
    ...(opts.autoctxVersion ? { autoctxVersion: opts.autoctxVersion } : {}),
    ...(opts.gitDetector ? { gitDetector: opts.gitDetector } : {}),
    ...(opts.branchExecutor ? { branchExecutor: opts.branchExecutor } : {}),
  };

  let result: InstrumentResult;
  try {
    result = await runInstrument(inputs);
  } catch (err) {
    return {
      stdout: "",
      stderr: `instrument failed: ${err instanceof Error ? err.message : String(err)}`,
      exitCode: 14,
    };
  }

  const payload = {
    sessionUlid: result.sessionUlid,
    sessionDir: result.sessionDir,
    mode: result.mode,
    filesScanned: result.filesScanned,
    filesAffected: result.filesAffected,
    callSitesDetected: result.callSitesDetected,
    filesSkipped: result.filesSkipped.map((f) => ({ path: f.path, reason: f.reason })),
    conflicts: result.conflicts.map((c) => c.kind),
    ...(result.applyResult !== undefined ? { applyResult: result.applyResult } : {}),
    planHash: result.planHash,
    summary: result.summary,
    exitCode: result.exitCode,
  };

  const stdoutPayload = formatOutput(payload, flags.output);
  const stderrMsgs: string[] = [];
  if (result.exitCode === 13) {
    stderrMsgs.push("Plugin conflict detected:");
    for (const c of result.conflicts) {
      stderrMsgs.push(`  - ${c.kind}`);
    }
  }
  if (result.exitCode !== 0 && result.summary && result.exitCode !== 13) {
    stderrMsgs.push(result.summary);
  }
  if (flags.force) {
    stderrMsgs.push(
      "WARNING: --force bypasses the clean-tree preflight — review the diff before committing.",
    );
  }
  if (flags.enhanced) {
    stderrMsgs.push(
      "Note: --enhanced requested. Enhancement runs only when an LLM provider is wired; "
      + "otherwise pr-body.md renders from deterministic defaults. plan.json is unaffected.",
    );
  }

  return {
    stdout: stdoutPayload,
    stderr: stderrMsgs.join("\n"),
    exitCode: result.exitCode,
  };
}

// ---------------------------------------------------------------------------
// Flag parsing
// ---------------------------------------------------------------------------

interface ParsedFlags {
  readonly mode: InstrumentMode;
  readonly branchName?: string;
  readonly commitMessage?: string;
  readonly excludes: readonly string[];
  readonly excludeFrom?: string;
  readonly maxFileBytes?: number;
  readonly failIfEmpty: boolean;
  readonly force: boolean;
  readonly enhanced: boolean;
  readonly output: OutputMode;
}

type ParseResult =
  | { readonly value: ParsedFlags }
  | { readonly error: string; readonly exitCode?: number };

const VALUE_FLAGS = new Set([
  "--branch",
  "--commit",
  "--exclude",
  "--exclude-from",
  "--max-file-bytes",
  "--output",
]);

const BOOL_FLAGS = new Set([
  "--dry-run",
  "--apply",
  "--fail-if-empty",
  "--force",
  "--enhanced",
]);

function parseInstrumentFlags(argv: readonly string[]): ParseResult {
  let dryRun = false;
  let apply = false;
  let branch: string | undefined;
  let commit: string | undefined;
  const excludes: string[] = [];
  let excludeFrom: string | undefined;
  let maxFileBytes: number | undefined;
  let failIfEmpty = false;
  let force = false;
  let enhanced = false;
  let output: OutputMode = "pretty";

  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i]!;
    if (!a.startsWith("--")) {
      return { error: `Unknown positional argument: ${a}`, exitCode: 11 };
    }
    if (!BOOL_FLAGS.has(a) && !VALUE_FLAGS.has(a)) {
      return { error: `Unknown flag: ${a}`, exitCode: 11 };
    }
    if (BOOL_FLAGS.has(a)) {
      switch (a) {
        case "--dry-run":
          dryRun = true;
          break;
        case "--apply":
          apply = true;
          break;
        case "--fail-if-empty":
          failIfEmpty = true;
          break;
        case "--force":
          force = true;
          break;
        case "--enhanced":
          enhanced = true;
          break;
      }
      continue;
    }
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) {
      return { error: `Flag ${a} requires a value`, exitCode: 11 };
    }
    i += 1;
    switch (a) {
      case "--branch":
        branch = next;
        break;
      case "--commit":
        commit = next;
        break;
      case "--exclude":
        excludes.push(next);
        break;
      case "--exclude-from":
        excludeFrom = next;
        break;
      case "--max-file-bytes": {
        const n = Number.parseInt(next, 10);
        if (!Number.isFinite(n) || n <= 0) {
          return {
            error: `--max-file-bytes requires a positive integer, got: ${next}`,
            exitCode: 11,
          };
        }
        maxFileBytes = n;
        break;
      }
      case "--output": {
        if (next !== "json" && next !== "table" && next !== "pretty") {
          return {
            error: `--output must be json|table|pretty, got: ${next}`,
            exitCode: 11,
          };
        }
        output = next;
        break;
      }
    }
  }

  // Modes are mutually exclusive.
  if (dryRun && apply) {
    return { error: "--dry-run and --apply are mutually exclusive", exitCode: 11 };
  }
  if (branch !== undefined && !apply) {
    return { error: "--branch requires --apply", exitCode: 11 };
  }
  if (commit !== undefined && !apply) {
    return { error: "--commit requires --apply", exitCode: 11 };
  }

  let mode: InstrumentMode;
  if (apply && branch !== undefined) mode = "apply-branch";
  else if (apply) mode = "apply";
  else mode = "dry-run";

  const value: ParsedFlags = {
    mode,
    excludes,
    failIfEmpty,
    force,
    enhanced,
    output,
    ...(branch !== undefined ? { branchName: branch } : {}),
    ...(commit !== undefined ? { commitMessage: commit } : {}),
    ...(excludeFrom !== undefined ? { excludeFrom } : {}),
    ...(maxFileBytes !== undefined ? { maxFileBytes } : {}),
  };
  return { value };
}
