// `autoctx registry ...` subcommand group.
//
// Responsibilities:
//   - repair  : rebuild state/active/ pointers by scanning every artifact's history.
//   - validate: structural validation report for the whole registry.
//   - migrate : stub; real impl in Layer 11.

import { openRegistry } from "../registry/index.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import type { CliContext, CliResult } from "./types.js";

export const REGISTRY_HELP_TEXT = `autoctx registry — registry maintenance commands

Subcommands:
  repair     Rebuild state pointers from scratch (idempotent)
  validate   Validate the registry and print a structured report
  migrate    [not-implemented; Layer 11]

Examples:
  autoctx registry repair
  autoctx registry validate --output json
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
      return {
        stdout: "",
        stderr: "Layer 11 — not implemented",
        exitCode: EXIT.NOT_IMPLEMENTED,
      };
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
