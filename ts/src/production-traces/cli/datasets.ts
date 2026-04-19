// `autoctx production-traces datasets list | show`
//
// Enumerates or inspects dataset manifests produced by `build-dataset`. The
// on-disk layout is `.autocontext/datasets/<datasetId>/manifest.json` (spec §8.4).

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import { parseFlags, stringFlag } from "./_shared/flags.js";
import type { CliContext, CliResult } from "./_shared/types.js";

export const DATASETS_HELP_TEXT = `autoctx production-traces datasets — inspect generated datasets

Subcommands:
  list       List dataset manifests under .autocontext/datasets/
  show       Render a specific dataset's manifest

Usage:
  autoctx production-traces datasets list [--output json|pretty|table]
  autoctx production-traces datasets show <datasetId> [--output json|pretty]
`;

export async function runDatasets(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const sub = args[0];
  if (!sub || sub === "--help" || sub === "-h") {
    return { stdout: DATASETS_HELP_TEXT, stderr: "", exitCode: EXIT.SUCCESS };
  }
  switch (sub) {
    case "list":
      return runDatasetsList(args.slice(1), ctx);
    case "show":
      return runDatasetsShow(args.slice(1), ctx);
    default:
      return {
        stdout: "",
        stderr: `Unknown datasets subcommand: ${sub}\n${DATASETS_HELP_TEXT}`,
        exitCode: EXIT.DOMAIN_FAILURE,
      };
  }
}

async function runDatasetsList(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const flags = parseFlags(args, { output: { type: "string", default: "pretty" } });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;

  const root = join(ctx.cwd, ".autocontext", "datasets");
  if (!existsSync(root)) {
    return {
      stdout: formatOutput([], output),
      stderr: "",
      exitCode: EXIT.SUCCESS,
    };
  }

  const rows: Array<{
    datasetId: string;
    name: string;
    createdAt: string;
    traceCount: number;
    train: number;
    eval: number;
    holdout: number;
  }> = [];
  for (const entry of readdirSync(root).sort()) {
    const manifestPath = join(root, entry, "manifest.json");
    if (!existsSync(manifestPath) || !statSync(manifestPath).isFile()) continue;
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(readFileSync(manifestPath, "utf-8")) as Record<string, unknown>;
    } catch {
      continue;
    }
    const source = (parsed.source ?? {}) as Record<string, unknown>;
    const splits = (parsed.splits ?? {}) as Record<string, Record<string, unknown>>;
    rows.push({
      datasetId: String(parsed.datasetId ?? entry),
      name: String(parsed.name ?? ""),
      createdAt: String(parsed.createdAt ?? ""),
      traceCount: Number(source.traceCount ?? 0),
      train: Number(splits.train?.rowCount ?? 0),
      eval: Number(splits.eval?.rowCount ?? 0),
      holdout: Number(splits.holdout?.rowCount ?? 0),
    });
  }
  return {
    stdout: formatOutput(rows, output),
    stderr: "",
    exitCode: EXIT.SUCCESS,
  };
}

async function runDatasetsShow(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const id = args[0];
  if (!id || id.startsWith("--")) {
    return {
      stdout: "",
      stderr: "Usage: autoctx production-traces datasets show <datasetId> [--output ...]",
      exitCode: EXIT.DOMAIN_FAILURE,
    };
  }
  const flags = parseFlags(args.slice(1), { output: { type: "string", default: "pretty" } });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;

  const manifestPath = join(ctx.cwd, ".autocontext", "datasets", id, "manifest.json");
  if (!existsSync(manifestPath)) {
    return {
      stdout: "",
      stderr: `dataset not found: ${id}`,
      exitCode: EXIT.NO_MATCHING_TRACES,
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(manifestPath, "utf-8"));
  } catch (err) {
    return {
      stdout: "",
      stderr: `dataset manifest malformed at ${manifestPath}: ${err instanceof Error ? err.message : String(err)}`,
      exitCode: EXIT.INVALID_CONFIG,
    };
  }
  return {
    stdout: formatOutput(parsed, output),
    stderr: "",
    exitCode: EXIT.SUCCESS,
  };
}
