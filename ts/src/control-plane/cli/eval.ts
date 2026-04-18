// `autoctx eval ...` subcommand group.
//
// Responsibilities: attach / list for EvalRuns on existing Artifacts.

import { readFileSync, existsSync } from "node:fs";
import type { EvalRun, MetricBundle } from "../contract/types.js";
import {
  parseArtifactId,
  parseSuiteId,
  type SuiteId,
} from "../contract/branded-ids.js";
import { createEvalRun } from "../contract/factories.js";
import { openRegistry } from "../registry/index.js";
import { attachEvalRun, EvalRunAlreadyAttachedError } from "../eval-ingest/index.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import type { CliContext, CliResult } from "./types.js";

export const EVAL_HELP_TEXT = `autoctx eval — attach and inspect EvalRuns

Subcommands:
  attach     Attach a metrics bundle to an artifact for a given suite
  list       List EvalRuns attached to an artifact

Examples:
  autoctx eval attach <artifactId> --suite prod-eval \\
      --metrics ./metrics.json --dataset-provenance ./dataset.json \\
      [--run-id run_1]
  autoctx eval list <artifactId> --output json
`;

export async function runEval(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const sub = args[0];
  if (!sub || sub === "--help" || sub === "-h") {
    return { stdout: EVAL_HELP_TEXT, stderr: "", exitCode: 0 };
  }
  switch (sub) {
    case "attach":
      return runAttach(args.slice(1), ctx);
    case "list":
      return runList(args.slice(1), ctx);
    default:
      return {
        stdout: "",
        stderr: `Unknown eval subcommand: ${sub}\n${EVAL_HELP_TEXT}`,
        exitCode: EXIT.HARD_FAIL,
      };
  }
}

async function runAttach(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const id = args[0];
  if (!id || id.startsWith("--")) {
    return { stdout: "", stderr: "Usage: autoctx eval attach <artifactId> --suite <id> --metrics <path> --dataset-provenance <path>", exitCode: EXIT.HARD_FAIL };
  }
  const artifactId = parseArtifactId(id);
  if (artifactId === null) {
    return { stdout: "", stderr: `Invalid artifact id: ${id}`, exitCode: EXIT.INVALID_ARTIFACT };
  }

  const flags = parseSimpleFlags(args.slice(1), [
    "suite",
    "metrics",
    "dataset-provenance",
    "run-id",
    "output",
  ]);
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  }
  const { suite, metrics, "dataset-provenance": dpPath, "run-id": explicitRunId, output } = flags.value;

  if (!suite || !metrics || !dpPath) {
    return {
      stdout: "",
      stderr: "eval attach requires --suite, --metrics, and --dataset-provenance",
      exitCode: EXIT.HARD_FAIL,
    };
  }

  const suiteId = parseSuiteId(suite);
  if (suiteId === null) {
    return { stdout: "", stderr: `Invalid suiteId: ${suite}`, exitCode: EXIT.HARD_FAIL };
  }

  const metricsPath = ctx.resolve(metrics);
  const dpAbs = ctx.resolve(dpPath);
  if (!existsSync(metricsPath)) {
    return { stdout: "", stderr: `metrics file not found: ${metricsPath}`, exitCode: EXIT.IO_ERROR };
  }
  if (!existsSync(dpAbs)) {
    return { stdout: "", stderr: `dataset-provenance file not found: ${dpAbs}`, exitCode: EXIT.IO_ERROR };
  }

  let parsedMetrics: MetricBundle;
  let parsedDp: EvalRun["datasetProvenance"];
  try {
    parsedMetrics = JSON.parse(readFileSync(metricsPath, "utf-8")) as MetricBundle;
    parsedDp = JSON.parse(readFileSync(dpAbs, "utf-8")) as EvalRun["datasetProvenance"];
  } catch (err) {
    return { stdout: "", stderr: `JSON parse error: ${err instanceof Error ? err.message : String(err)}`, exitCode: EXIT.HARD_FAIL };
  }

  const runId = explicitRunId ?? `cli_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const evalRun = createEvalRun({
    runId,
    artifactId,
    suiteId: suiteId as SuiteId,
    metrics: parsedMetrics,
    datasetProvenance: parsedDp,
    ingestedAt: ctx.now(),
  });

  const registry = openRegistry(ctx.cwd);

  try {
    const result = await attachEvalRun(registry, evalRun);
    const mode = (output ?? "pretty") as OutputMode;
    return {
      stdout: formatOutput(
        { artifactId: result.artifact.id, runId: result.evalRun.runId, suiteId: result.evalRun.suiteId, evalRunCount: result.artifact.evalRuns.length },
        mode,
      ),
      stderr: "",
      exitCode: EXIT.PASS_STRONG_OR_MODERATE,
    };
  } catch (err) {
    if (err instanceof EvalRunAlreadyAttachedError) {
      return {
        stdout: "",
        stderr: err.message,
        exitCode: EXIT.HARD_FAIL,
      };
    }
    return {
      stdout: "",
      stderr: err instanceof Error ? err.message : String(err),
      exitCode: EXIT.VALIDATION_FAILED,
    };
  }
}

async function runList(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const id = args[0];
  if (!id || id.startsWith("--")) {
    return { stdout: "", stderr: "Usage: autoctx eval list <artifactId>", exitCode: EXIT.HARD_FAIL };
  }
  const artifactId = parseArtifactId(id);
  if (artifactId === null) {
    return { stdout: "", stderr: `Invalid artifact id: ${id}`, exitCode: EXIT.INVALID_ARTIFACT };
  }
  const flags = parseSimpleFlags(args.slice(1), ["output"]);
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  }
  const mode = ((flags.value.output ?? "pretty") as OutputMode);
  const registry = openRegistry(ctx.cwd);
  try {
    const artifact = registry.loadArtifact(artifactId);
    return {
      stdout: formatOutput(artifact.evalRuns, mode),
      stderr: "",
      exitCode: EXIT.PASS_STRONG_OR_MODERATE,
    };
  } catch (err) {
    return {
      stdout: "",
      stderr: err instanceof Error ? err.message : String(err),
      exitCode: EXIT.INVALID_ARTIFACT,
    };
  }
}

// ---- helpers ----

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
