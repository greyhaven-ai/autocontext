// `autoctx registry ...` subcommand group.
//
// Responsibilities:
//   - repair  : rebuild state/active/ pointers by scanning every artifact's history.
//   - validate: structural validation report for the whole registry.
//   - migrate : import legacy ModelRecord documents into the control-plane registry.

import { isAbsolute, resolve as pathResolve } from "node:path";
import { importLegacyModelRecords } from "../actuators/fine-tuned-model/legacy-adapter.js";
import { openRegistry } from "../registry/index.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import type { CliContext, CliResult } from "./types.js";

export const REGISTRY_HELP_TEXT = `autoctx registry — registry maintenance commands

Subcommands:
  repair     Rebuild state pointers from scratch (idempotent)
  validate   Validate the registry and print a structured report
  migrate    Import legacy ModelRecord documents as fine-tuned-model artifacts

Examples:
  autoctx registry repair
  autoctx registry validate --output json
  autoctx registry migrate --from ./legacy.json --output json
`;

export const REGISTRY_MIGRATE_HELP_TEXT = `autoctx registry migrate — import legacy ModelRecord documents

Usage:
  autoctx registry migrate [--from <path>] [--output pretty|json]

Flags:
  --from      Path to a JSON file containing an array of legacy ModelRecord
              documents. If omitted, the adapter looks for
              <cwd>/.autocontext/legacy-model-records.json. A missing default
              file is a graceful no-op; a missing explicit file is an error.
  --output    pretty (default) or json

Behavior:
  Each record is mapped to a fine-tuned-model Artifact. Records whose id is
  already present in the registry are reported as 'skipped' (idempotent).
  Per-record failures are collected into 'errors' — one bad record does not
  abort the batch.

Exit codes:
  0   clean run (errors array empty)
  1   one or more per-record errors (other records may have imported)
  10+ infrastructure faults (lock contention, I/O, etc.)
`;

export async function runRegistryOps(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const sub = args[0];
  if (!sub || sub === "--help" || sub === "-h") {
    return { stdout: REGISTRY_HELP_TEXT, stderr: "", exitCode: 0 };
  }
  switch (sub) {
    case "repair":
      return runRepair(ctx);
    case "validate":
      return runValidate(args.slice(1), ctx);
    case "migrate":
      return runMigrate(args.slice(1), ctx);
    default:
      return {
        stdout: "",
        stderr: `Unknown registry subcommand: ${sub}\n${REGISTRY_HELP_TEXT}`,
        exitCode: EXIT.HARD_FAIL,
      };
  }
}

async function runRepair(ctx: CliContext): Promise<CliResult> {
  const registry = openRegistry(ctx.cwd);
  try {
    registry.repair();
  } catch (err) {
    return { stdout: "", stderr: err instanceof Error ? err.message : String(err), exitCode: EXIT.IO_ERROR };
  }
  return { stdout: "Registry repair complete.", stderr: "", exitCode: EXIT.PASS_STRONG_OR_MODERATE };
}

async function runValidate(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const flags = parseSimpleFlags(args, ["output"]);
  if ("error" in flags) return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  const mode = (flags.value.output ?? "pretty") as OutputMode;

  const registry = openRegistry(ctx.cwd);
  const report = registry.validate();

  return {
    stdout: formatOutput(report, mode),
    stderr: "",
    exitCode: report.ok ? EXIT.PASS_STRONG_OR_MODERATE : EXIT.VALIDATION_FAILED,
  };
}

async function runMigrate(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  if (args[0] === "--help" || args[0] === "-h") {
    return { stdout: REGISTRY_MIGRATE_HELP_TEXT, stderr: "", exitCode: 0 };
  }
  const flags = parseSimpleFlags(args, ["from", "output"]);
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  }
  const mode = (flags.value.output ?? "pretty") as OutputMode;

  const fromRaw = flags.value.from;
  const fromPath = fromRaw === undefined
    ? undefined
    : isAbsolute(fromRaw)
      ? fromRaw
      : pathResolve(ctx.cwd, fromRaw);

  let registry;
  try {
    registry = openRegistry(ctx.cwd);
  } catch (err) {
    return {
      stdout: "",
      stderr: `Failed to open registry at ${ctx.cwd}: ${err instanceof Error ? err.message : String(err)}`,
      exitCode: EXIT.IO_ERROR,
    };
  }

  let result;
  try {
    result = await importLegacyModelRecords(
      ctx.cwd,
      registry,
      fromPath !== undefined ? { fromPath } : {},
    );
  } catch (err) {
    // The adapter is documented to never throw for per-record failures, but a
    // programming-error exception (e.g. scratch-dir creation failure) could
    // still bubble up. Treat as an I/O fault — distinct from a record-level
    // error which uses exit code 1.
    return {
      stdout: "",
      stderr: `registry migrate failed: ${err instanceof Error ? err.message : String(err)}`,
      exitCode: EXIT.IO_ERROR,
    };
  }

  const hasErrors = result.errors.length > 0;
  const exitCode = hasErrors ? EXIT.HARD_FAIL : EXIT.PASS_STRONG_OR_MODERATE;

  if (mode === "json") {
    return {
      stdout: formatOutput(result, "json"),
      stderr: "",
      exitCode,
    };
  }
  return {
    stdout: renderPrettyMigrate(result),
    stderr: "",
    exitCode,
  };
}

function renderPrettyMigrate(result: {
  readonly imported: number;
  readonly skipped: number;
  readonly errors: readonly { readonly id: string; readonly reason: string }[];
}): string {
  const lines: string[] = [];
  lines.push(`Legacy-record migration summary:`);
  lines.push(`  imported: ${result.imported}`);
  lines.push(`  skipped:  ${result.skipped}`);
  lines.push(`  errors:   ${result.errors.length}`);
  if (result.errors.length > 0) {
    lines.push("");
    lines.push("Errors:");
    for (const e of result.errors) {
      lines.push(`  - ${e.id}: ${e.reason}`);
    }
  }
  return lines.join("\n");
}

function parseSimpleFlags(
  args: readonly string[],
  known: readonly string[],
): { value: Record<string, string | undefined> } | { error: string } {
  const result: Record<string, string | undefined> = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i]!;
    if (!a.startsWith("--")) continue;
    const name = a.slice(2);
    if (!known.includes(name)) return { error: `Unknown flag: --${name}` };
    const next = args[i + 1];
    if (next === undefined || next.startsWith("--")) return { error: `Flag --${name} requires a value` };
    result[name] = next;
    i += 1;
  }
  for (const k of known) {
    if (!(k in result)) result[k] = undefined;
  }
  return { value: result };
}
