/**
 * Public surface of the autocontext `probes` CLI namespace.
 *
 * Mirrors the production-traces / instrument CLI pattern:
 *   - In-process dispatch, no `process.exit` / `console` inside handlers.
 *   - Handlers return { stdout, stderr, exitCode }; the outer CLI adapter
 *     in ts/src/cli/index.ts prints and exits.
 *   - Tests consume the runner directly for speed (no subprocess spawn).
 *
 * Slice 6 ships `check`. Slice 7 adds `extract` -- read a harness trace
 * and synthesize a runnable probe suite by joining observations with
 * caller-declared expectations.
 */

import { CHECK_HELP_TEXT, runCheck } from "./check.js";
import {
  EXTRACT_HELP_TEXT,
  extractContractProbeSuite,
  HarnessTraceSchema,
  runExtract,
} from "./extract.js";
import type { ProbesCheckResult } from "./check.js";
import type { HarnessTrace, ProbesExtractResult } from "./extract.js";

export { CHECK_HELP_TEXT, runCheck };
export { EXTRACT_HELP_TEXT, extractContractProbeSuite, HarnessTraceSchema, runExtract };
export type { HarnessTrace, ProbesCheckResult, ProbesExtractResult };

export type ProbesCommandResult = ProbesCheckResult | ProbesExtractResult;

const TOP_HELP = `autoctx probes -- check observed harness state against contract probes.

Subcommands:
  check     Run a JSON-defined probe suite and report pass/fail per probe.
  extract   Synthesize a runnable probe suite from a harness trace
            (observations + operator expectations).

Run \`autoctx probes <subcommand> --help\` for details.

The harness-trace and probe-suite JSON formats are documented in the
"Contract Probes" section of the autoctx README.
`;

export async function runProbesCommand(args: readonly string[]): Promise<ProbesCommandResult> {
  const subcommand = args[0];
  const rest = args.slice(1);

  if (subcommand === undefined || subcommand === "--help" || subcommand === "-h") {
    return { stdout: TOP_HELP, stderr: "", exitCode: 0 };
  }

  switch (subcommand) {
    case "check":
      return runCheck(rest);
    case "extract":
      return runExtract(rest);
    default:
      return {
        stdout: "",
        stderr: `autoctx probes: unknown subcommand ${JSON.stringify(subcommand)}\n\n${TOP_HELP}`,
        exitCode: 1,
      };
  }
}
