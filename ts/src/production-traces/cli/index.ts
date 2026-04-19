// Public surface of the autocontext production-traces CLI namespace.
//
// Mirrors the Foundation B `runControlPlaneCommand` runner pattern:
//   - In-process dispatch, no `process.exit` / `console` inside handlers.
//   - Handlers return { stdout, stderr, exitCode } — the outer CLI adapter
//     prints and exits.
//   - Tests consume the runner directly for speed (no subprocess spawn).
//
// The import-surface is deliberately narrow: only the runner, help text, and
// the shared exit-code / output-formatter re-exports are public. Internals
// (individual command modules) are not re-exported to keep the blast radius
// small for future refactors.

import { resolve as pathResolve } from "node:path";
import { EXIT } from "./_shared/exit-codes.js";
import type { CliContext, CliResult } from "./_shared/types.js";

import { runInit, INIT_HELP_TEXT } from "./init.js";
import { runIngest, INGEST_HELP_TEXT } from "./ingest.js";
import {
  runList,
  runShow,
  runStats,
  LIST_HELP_TEXT,
  SHOW_HELP_TEXT,
  STATS_HELP_TEXT,
} from "./list-show-stats.js";
import { runBuildDataset, BUILD_DATASET_HELP_TEXT } from "./build-dataset.js";
import { runDatasets, DATASETS_HELP_TEXT } from "./datasets.js";
import { runExport, EXPORT_HELP_TEXT } from "./export.js";
import { runPolicy, POLICY_HELP_TEXT } from "./policy.js";
import { runRotateSalt, ROTATE_SALT_HELP_TEXT } from "./rotate-salt.js";
import { runPrune, PRUNE_HELP_TEXT } from "./prune.js";

export { EXIT } from "./_shared/exit-codes.js";
export type { ExitCode } from "./_shared/exit-codes.js";
export { formatOutput } from "./_shared/output-formatters.js";
export type { OutputMode } from "./_shared/output-formatters.js";
export type { CliContext, CliResult } from "./_shared/types.js";

export {
  INIT_HELP_TEXT,
  INGEST_HELP_TEXT,
  LIST_HELP_TEXT,
  SHOW_HELP_TEXT,
  STATS_HELP_TEXT,
  BUILD_DATASET_HELP_TEXT,
  DATASETS_HELP_TEXT,
  EXPORT_HELP_TEXT,
  POLICY_HELP_TEXT,
  ROTATE_SALT_HELP_TEXT,
  PRUNE_HELP_TEXT,
};

const TOP_HELP = `autoctx production-traces — ingest, curate, redact, and export production LLM traces

Subcommands:
  init            Scaffold .autocontext/production-traces/ and generate install-salt
  ingest          Validate & move incoming/ batches to ingested/ (shared lock)
  list            List stored traces (local view, no redaction)
  show            Inspect a single trace (add --as-exported to preview redaction)
  stats           Aggregate counts by env / app / provider / outcome / cluster
  build-dataset   Generate an evaluation dataset from curated traces (AC-541)
  datasets        List or show generated datasets
  export          Export traces outbound with redaction applied
  policy          Show or set the redaction-mode policy (§7.4)
  rotate-salt     Rotate install-salt (break-glass; requires --force)
  prune           Enforce retention policy out-of-band

Run \`autoctx production-traces <subcommand> --help\` for details.
`;

export interface RunProductionTracesOptions {
  /** Working directory override; defaults to process.cwd(). */
  readonly cwd?: string;
  /** Optional now() override for deterministic tests. */
  readonly now?: () => string;
}

/**
 * Entry point: dispatch a production-traces subcommand.
 *
 * `argv` is the args *after* the top-level `production-traces` keyword.
 * For example, running:
 *     autoctx production-traces ingest --strict
 * the caller passes:
 *     runProductionTracesCommand(["ingest", "--strict"], { cwd })
 *
 * Returns a CliResult. The outer CLI (`ts/src/cli/index.ts`) prints
 * stdout/stderr and exits with exitCode. Tests consume CliResult directly.
 */
export async function runProductionTracesCommand(
  argv: readonly string[],
  opts: RunProductionTracesOptions = {},
): Promise<CliResult> {
  const cwd = opts.cwd ?? process.cwd();
  const nowFn = opts.now ?? (() => new Date().toISOString());
  const ctx: CliContext = {
    cwd,
    resolve: (p) => pathResolve(cwd, p),
    now: nowFn,
  };

  const sub = argv[0];
  if (!sub || sub === "--help" || sub === "-h") {
    return { stdout: TOP_HELP, stderr: "", exitCode: EXIT.SUCCESS };
  }
  switch (sub) {
    case "init":
      return runInit(argv.slice(1), ctx);
    case "ingest":
      return runIngest(argv.slice(1), ctx);
    case "list":
      return runList(argv.slice(1), ctx);
    case "show":
      return runShow(argv.slice(1), ctx);
    case "stats":
      return runStats(argv.slice(1), ctx);
    case "build-dataset":
      return runBuildDataset(argv.slice(1), ctx);
    case "datasets":
      return runDatasets(argv.slice(1), ctx);
    case "export":
      return runExport(argv.slice(1), ctx);
    case "policy":
      return runPolicy(argv.slice(1), ctx);
    case "rotate-salt":
      return runRotateSalt(argv.slice(1), ctx);
    case "prune":
      return runPrune(argv.slice(1), ctx);
    default:
      return {
        stdout: "",
        stderr: `Unknown production-traces subcommand: ${sub}\n${TOP_HELP}`,
        exitCode: EXIT.DOMAIN_FAILURE,
      };
  }
}
