// `autoctx production-traces rotate-salt --force`
//
// Unconditional overwrite of `.autocontext/install-salt`. Because salt rotation
// invalidates all previously-hashed identifiers (userIdHash / sessionIdHash /
// categoryOverride 'hash' action outputs), the CLI REQUIRES `--force` to run.
//
// Emits a prominent break-glass advisory on stderr and records the rotation
// timestamp in stdout. See spec §4.6 for the full contract.

import { rotateInstallSalt } from "../redaction/index.js";
import { acquireLock } from "../ingest/lock.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import { parseFlags, stringFlag, booleanFlag } from "./_shared/flags.js";
import type { CliContext, CliResult } from "./_shared/types.js";

export const ROTATE_SALT_HELP_TEXT = `autoctx production-traces rotate-salt — generate a new install-salt

Usage:
  autoctx production-traces rotate-salt --force [--output json|pretty]

Critical infrastructure warning (spec §4.6):
  Rotation invalidates ALL previously-hashed identifiers:
    - userIdHash / sessionIdHash on existing traces no longer join to new ones.
    - Any field hashed via categoryOverride 'hash' action (e.g. pii-email) is
      non-correlatable across the rotation boundary.
  This is the break-glass recovery path — only use after a confirmed salt leak.
`;

export async function runRotateSalt(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  if (args[0] === "--help" || args[0] === "-h") {
    return { stdout: ROTATE_SALT_HELP_TEXT, stderr: "", exitCode: EXIT.SUCCESS };
  }
  const flags = parseFlags(args, {
    force: { type: "boolean" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const force = booleanFlag(flags.value, "force");
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;

  if (!force) {
    return {
      stdout: "",
      stderr:
        "rotate-salt requires --force. This operation invalidates all previously-" +
        "hashed identifiers (userIdHash, sessionIdHash, category-override 'hash' " +
        "outputs). Re-run with --force after reading spec §4.6.",
      exitCode: EXIT.DOMAIN_FAILURE,
    };
  }

  let lock;
  try {
    lock = acquireLock(ctx.cwd);
  } catch (err) {
    return {
      stdout: "",
      stderr: `rotate-salt: lock timeout: ${msgOf(err)}`,
      exitCode: EXIT.LOCK_TIMEOUT,
    };
  }

  try {
    await rotateInstallSalt(ctx.cwd);
  } catch (err) {
    return {
      stdout: "",
      stderr: `rotate-salt: ${msgOf(err)}`,
      exitCode: EXIT.IO_FAILURE,
    };
  } finally {
    lock.release();
  }

  const stderrAdvisory =
    "BREAK-GLASS ADVISORY: install-salt rotated. " +
    "All previously-hashed userIdHash / sessionIdHash values are now " +
    "non-correlatable with new traces. Any downstream joins across the " +
    "rotation boundary will break. See spec §4.6.";
  return {
    stdout: formatOutput({ rotatedAt: ctx.now(), ok: true }, output),
    stderr: stderrAdvisory,
    exitCode: EXIT.SUCCESS,
  };
}

function msgOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
