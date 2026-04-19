// `autoctx production-traces policy show | set`
//
// Thin wrapper over Layer 4's `loadRedactionPolicy` / `saveRedactionPolicy`.
// Implements spec §7.4's mode-change safety rails:
//
//   - Switching from `on-ingest` to `on-export` requires `--force` and
//     prints a prominent break-glass advisory (the switch does NOT recover
//     already-redacted data — operators must understand what they're doing).
//
//   - Switching from `on-export` to `on-ingest` is allowed without --force
//     but STILL prints an advisory noting the defense-in-depth trade-off
//     (loss of incident-debuggability on stored traces).

import {
  loadRedactionPolicy,
  saveRedactionPolicy,
} from "../redaction/index.js";
import type { LoadedRedactionPolicy } from "../redaction/types.js";
import { acquireLock } from "../ingest/lock.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import { parseFlags, stringFlag, booleanFlag } from "./_shared/flags.js";
import type { CliContext, CliResult } from "./_shared/types.js";

export const POLICY_HELP_TEXT = `autoctx production-traces policy — redaction policy management

Subcommands:
  show     Print the current redaction policy (default if no file: built-ins)
  set      Change the redaction mode

Usage:
  autoctx production-traces policy show [--output json|pretty|table]
  autoctx production-traces policy set --mode on-export|on-ingest [--force]

Mode transitions (spec §7.4):
  on-export  → on-ingest  : allowed; prints an advisory warning.
  on-ingest  → on-export  : requires --force; previously-redacted data does
                             NOT return to plaintext.
`;

export async function runPolicy(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const sub = args[0];
  if (!sub || sub === "--help" || sub === "-h") {
    return { stdout: POLICY_HELP_TEXT, stderr: "", exitCode: EXIT.SUCCESS };
  }
  switch (sub) {
    case "show":
      return runPolicyShow(args.slice(1), ctx);
    case "set":
      return runPolicySet(args.slice(1), ctx);
    default:
      return {
        stdout: "",
        stderr: `Unknown policy subcommand: ${sub}\n${POLICY_HELP_TEXT}`,
        exitCode: EXIT.DOMAIN_FAILURE,
      };
  }
}

async function runPolicyShow(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const flags = parseFlags(args, { output: { type: "string", default: "pretty" } });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;

  let policy: LoadedRedactionPolicy;
  try {
    policy = await loadRedactionPolicy(ctx.cwd);
  } catch (err) {
    return {
      stdout: "",
      stderr: `policy show: ${msgOf(err)}`,
      exitCode: EXIT.INVALID_CONFIG,
    };
  }
  return {
    stdout: formatOutput(policy, output),
    stderr: "",
    exitCode: EXIT.SUCCESS,
  };
}

async function runPolicySet(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const flags = parseFlags(args, {
    mode: { type: "string", required: true },
    force: { type: "boolean" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const mode = stringFlag(flags.value, "mode")!;
  const force = booleanFlag(flags.value, "force");
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;

  if (!(mode === "on-export" || mode === "on-ingest")) {
    return {
      stdout: "",
      stderr: `invalid --mode '${mode}' (expected on-export|on-ingest)`,
      exitCode: EXIT.DOMAIN_FAILURE,
    };
  }

  let lock;
  try {
    lock = acquireLock(ctx.cwd);
  } catch (err) {
    return {
      stdout: "",
      stderr: `policy set: lock timeout: ${msgOf(err)}`,
      exitCode: EXIT.LOCK_TIMEOUT,
    };
  }
  try {
    let current: LoadedRedactionPolicy;
    try {
      current = await loadRedactionPolicy(ctx.cwd);
    } catch (err) {
      return {
        stdout: "",
        stderr: `policy set: ${msgOf(err)}`,
        exitCode: EXIT.INVALID_CONFIG,
      };
    }

    const stderrLines: string[] = [];
    if (current.mode === mode) {
      stderrLines.push(`policy mode already '${mode}' — no change.`);
    } else {
      if (current.mode === "on-ingest" && mode === "on-export") {
        if (!force) {
          return {
            stdout: "",
            stderr:
              "refusing to switch on-ingest → on-export without --force. " +
              "Already-redacted traces will NOT return to plaintext. " +
              "Re-run with --force once you've read spec §7.4.",
            exitCode: EXIT.DOMAIN_FAILURE,
          };
        }
        stderrLines.push(
          "WARNING: switching on-ingest → on-export. Already-redacted traces " +
          "on disk remain redacted — this change only affects future ingests.",
        );
      } else if (current.mode === "on-export" && mode === "on-ingest") {
        stderrLines.push(
          "WARNING: switching on-export → on-ingest. Traces ingested from now on " +
          "will be redacted BEFORE being written to ingested/. Debugging production " +
          "incidents from stored traces becomes significantly harder. See §7.4.",
        );
      }
    }

    const updated: LoadedRedactionPolicy = {
      ...current,
      mode: mode as LoadedRedactionPolicy["mode"],
    };
    try {
      await saveRedactionPolicy(ctx.cwd, updated);
    } catch (err) {
      return { stdout: "", stderr: `policy set: ${msgOf(err)}`, exitCode: EXIT.IO_FAILURE };
    }

    return {
      stdout: formatOutput({ mode: updated.mode }, output),
      stderr: stderrLines.join("\n"),
      exitCode: EXIT.SUCCESS,
    };
  } finally {
    lock.release();
  }
}

function msgOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
