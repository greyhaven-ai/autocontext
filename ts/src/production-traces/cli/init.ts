// `autoctx production-traces init` — scaffold the `.autocontext/production-traces/`
// directory tree, default `redaction-policy.json`, default `retention-policy.json`,
// and the `install-salt`.
//
// Idempotent: re-running reports what was created vs already present and does
// NOT rotate the install-salt (rotation requires explicit `rotate-salt --force`
// per spec §12 risk mitigation).

import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import {
  defaultRedactionPolicy,
  saveRedactionPolicy,
  redactionPolicyPath,
  initializeInstallSalt,
  installSaltPath,
} from "../redaction/index.js";
import { productionTracesRoot } from "../ingest/paths.js";
import { acquireLock } from "../ingest/lock.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import { parseFlags, stringFlag } from "./_shared/flags.js";
import {
  defaultRetentionPolicy,
  saveRetentionPolicy,
  retentionPolicyPath,
} from "../retention/index.js";
import type { CliContext, CliResult } from "./_shared/types.js";

export const INIT_HELP_TEXT = `autoctx production-traces init — scaffold .autocontext/production-traces/

Usage:
  autoctx production-traces init [--output json|pretty|table]

Behavior:
  Creates .autocontext/production-traces/{incoming,ingested,failed,gc} subdirs.
  Writes default redaction-policy.json (mode: on-export, auto-detect enabled).
  Writes default retention-policy.json (90d retention, preserve failures).
  Generates install-salt if missing (256-bit random; CRITICAL infrastructure).

Idempotent: re-running reports what was created vs already present. Never
rotates the install-salt — use 'autoctx production-traces rotate-salt --force'
for that (and read §4.6 / §12 first).
`;

interface InitReport {
  readonly cwd: string;
  readonly created: readonly string[];
  readonly alreadyPresent: readonly string[];
}

export async function runInit(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  if (args[0] === "--help" || args[0] === "-h") {
    return { stdout: INIT_HELP_TEXT, stderr: "", exitCode: EXIT.SUCCESS };
  }
  const flags = parseFlags(args, { output: { type: "string", default: "pretty" } });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;

  // init holds the shared lock so a concurrent ingest doesn't race on the
  // directory tree. Lock failures surface as exit 10.
  let lock;
  try {
    lock = acquireLock(ctx.cwd);
  } catch (err) {
    return {
      stdout: "",
      stderr: `init: could not acquire lock: ${msgOf(err)}`,
      exitCode: EXIT.LOCK_TIMEOUT,
    };
  }
  try {
    const created: string[] = [];
    const alreadyPresent: string[] = [];

    const root = productionTracesRoot(ctx.cwd);
    for (const sub of ["incoming", "ingested", "failed", "gc"]) {
      const path = join(root, sub);
      if (existsSync(path)) {
        alreadyPresent.push(path);
      } else {
        mkdirSync(path, { recursive: true });
        created.push(path);
      }
    }

    const policyPath = redactionPolicyPath(ctx.cwd);
    if (existsSync(policyPath)) {
      alreadyPresent.push(policyPath);
    } else {
      await saveRedactionPolicy(ctx.cwd, defaultRedactionPolicy());
      created.push(policyPath);
    }

    const retentionPath = retentionPolicyPath(ctx.cwd);
    if (existsSync(retentionPath)) {
      alreadyPresent.push(retentionPath);
    } else {
      await saveRetentionPolicy(ctx.cwd, defaultRetentionPolicy());
      created.push(retentionPath);
    }

    const saltPath = installSaltPath(ctx.cwd);
    if (existsSync(saltPath)) {
      alreadyPresent.push(saltPath);
    } else {
      try {
        await initializeInstallSalt(ctx.cwd);
        created.push(saltPath);
      } catch (err) {
        return {
          stdout: "",
          stderr: `init: failed to initialize install-salt: ${msgOf(err)}`,
          exitCode: EXIT.IO_FAILURE,
        };
      }
    }

    const report: InitReport = { cwd: ctx.cwd, created, alreadyPresent };
    return {
      stdout: formatOutput(report, output),
      stderr: "",
      exitCode: EXIT.SUCCESS,
    };
  } catch (err) {
    return {
      stdout: "",
      stderr: `init: ${msgOf(err)}`,
      exitCode: EXIT.IO_FAILURE,
    };
  } finally {
    lock.release();
  }
}

function msgOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
