// Public surface of the autocontext control-plane CLI layer.
// Import discipline (§3.2): CLI/ imports from everywhere in control-plane/.

import { resolve as pathResolve } from "node:path";
import { runCandidate, CANDIDATE_HELP_TEXT } from "./candidate.js";
import { runEval, EVAL_HELP_TEXT } from "./eval.js";
import { runPromotion, PROMOTION_HELP_TEXT } from "./promotion.js";
import { runRegistryOps, REGISTRY_HELP_TEXT } from "./registry-ops.js";
import { runEmitPr, EMIT_PR_HELP_TEXT } from "./emit-pr.js";
import { EXIT } from "./_shared/exit-codes.js";
import type { CliContext, CliResult } from "./types.js";

export { EXIT } from "./_shared/exit-codes.js";
export type { ExitCode } from "./_shared/exit-codes.js";
export { formatOutput } from "./_shared/output-formatters.js";
export type { OutputMode } from "./_shared/output-formatters.js";
export type { CliContext, CliResult } from "./types.js";
export {
  CANDIDATE_HELP_TEXT,
  EVAL_HELP_TEXT,
  PROMOTION_HELP_TEXT,
  REGISTRY_HELP_TEXT,
  EMIT_PR_HELP_TEXT,
};

// Importing actuators/index.js has the side effect of registering all four
// actuator types on the actuator registry. The CLI doesn't directly consume
// them in Layer 8 (they matter for the apply/emit pipeline in Layer 9+) but
// we import the module here so the registry is warm for any reachable command.
import "../actuators/index.js";

const TOP_HELP = `autoctx control-plane — evaluator-driven prompt/policy/routing/model management

Namespaces:
  candidate    Register, list, inspect, rollback Artifacts
  eval         Attach + list EvalRuns
  promotion    Decide, apply, inspect promotion transitions
  registry     Repair / validate / migrate

Top-level:
  emit-pr      Generate a promotion PR (or dry-run bundle) for a candidate

Run \`autoctx <namespace> --help\` for subcommand details.
`;

export interface RunControlPlaneOptions {
  /** Working directory override; defaults to process.cwd(). */
  readonly cwd?: string;
  /** Optional now() override for deterministic tests. */
  readonly now?: () => string;
}

/**
 * Entry point: dispatch a control-plane command.
 *
 * argv is the args *after* the top-level command (e.g. for
 *   `autoctx candidate list --scenario grid_ctf`
 * pass ["candidate", "list", "--scenario", "grid_ctf"]).
 *
 * Returns a CliResult. Callers (the outer CLI) print stdout/stderr and exit
 * with exitCode. Tests consume CliResult directly for speed.
 */
export async function runControlPlaneCommand(
  argv: readonly string[],
  opts: RunControlPlaneOptions = {},
): Promise<CliResult> {
  const cwd = opts.cwd ?? process.cwd();
  const nowFn = opts.now ?? (() => new Date().toISOString());
  const ctx: CliContext = {
    cwd,
    resolve: (p) => pathResolve(cwd, p),
    now: nowFn,
  };

  const namespace = argv[0];
  if (!namespace || namespace === "--help" || namespace === "-h") {
    return { stdout: TOP_HELP, stderr: "", exitCode: 0 };
  }
  switch (namespace) {
    case "candidate":
      return runCandidate(argv.slice(1), ctx);
    case "eval":
      return runEval(argv.slice(1), ctx);
    case "promotion":
      return runPromotion(argv.slice(1), ctx);
    case "registry":
      return runRegistryOps(argv.slice(1), ctx);
    case "emit-pr":
      return runEmitPr(argv.slice(1), ctx);
    default:
      return {
        stdout: "",
        stderr: `Unknown control-plane namespace: ${namespace}\n${TOP_HELP}`,
        exitCode: EXIT.HARD_FAIL,
      };
  }
}
