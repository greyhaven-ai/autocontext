/**
 * AC-679 (slice 2): trace-findings CLI workflow.
 *
 * Loads a `PublicTrace` from disk, runs the slice-1 extractor library, and
 * emits a TraceFindingReport as Markdown (default) or JSON. The handler is
 * pure -- returns `{stdout, stderr, exitCode}` instead of writing to
 * process streams -- so unit tests drive it directly without subprocess
 * spawn or stdout capture.
 *
 * Coupling to the production-traces storage layer (`--trace-id <id>`
 * against the ProductionTrace store) is intentionally deferred to a
 * later slice; for now the only input shape is `--trace <path>` to a
 * JSON file containing a PublicTrace.
 */

import { readFile, stat } from "node:fs/promises";
import { parseArgs, type ParseArgsConfig } from "node:util";

import {
  PublicTraceSchema,
  generateTraceFindingReport,
  renderTraceFindingReportMarkdown,
} from "../index.js";

export interface TraceFindingsCommandResult {
  readonly stdout: string;
  readonly stderr: string;
  readonly exitCode: number;
}

export const TRACE_FINDINGS_HELP_TEXT = `autoctx trace-findings — extract structured findings from a PublicTrace (AC-679)

Usage:
  autoctx trace-findings --trace <path> [--json]
  autoctx trace-findings --help

Options:
  --trace <path>   Path to a PublicTrace JSON file (required)
  --json           Emit the TraceFindingReport as JSON instead of Markdown
  -h, --help       Show this help

Output:
  Default: Markdown report (sections: Summary, Findings, Failure Motifs)
  --json:  TraceFindingReport JSON matching TraceFindingReportSchema`;

const PARSE_OPTIONS: ParseArgsConfig["options"] = {
  trace: { type: "string" },
  json: { type: "boolean" },
  help: { type: "boolean", short: "h" },
};

export async function runTraceFindingsCommand(
  args: readonly string[],
): Promise<TraceFindingsCommandResult> {
  if (args.length === 0 || args.includes("--help") || args.includes("-h")) {
    return ok(TRACE_FINDINGS_HELP_TEXT);
  }

  let parsed;
  try {
    parsed = parseArgs({
      args: [...args],
      options: PARSE_OPTIONS,
      strict: true,
      allowPositionals: false,
    });
  } catch (err) {
    return fail(`autoctx trace-findings: ${messageOf(err)}`);
  }

  const tracePath = stringFlag(parsed.values, "trace");
  if (!tracePath) {
    return fail("autoctx trace-findings: --trace <path> is required");
  }

  try {
    const stats = await stat(tracePath);
    if (!stats.isFile()) {
      return fail(`autoctx trace-findings: --trace path is not a file: ${tracePath}`);
    }
  } catch (err) {
    return fail(
      `autoctx trace-findings: could not read trace file ${tracePath}: ${messageOf(err)}`,
    );
  }

  let raw: string;
  try {
    raw = await readFile(tracePath, "utf8");
  } catch (err) {
    return fail(
      `autoctx trace-findings: could not read trace file ${tracePath}: ${messageOf(err)}`,
    );
  }

  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (err) {
    return fail(
      `autoctx trace-findings: could not parse JSON from ${tracePath}: ${messageOf(err)}`,
    );
  }

  const parseResult = PublicTraceSchema.safeParse(data);
  if (!parseResult.success) {
    const issues = parseResult.error.issues
      .map((issue) => `${issue.path.join(".") || "<root>"}: ${issue.message}`)
      .join("; ");
    return fail(`autoctx trace-findings: file is not a valid PublicTrace: ${issues}`);
  }

  const report = generateTraceFindingReport(parseResult.data);
  const wantJson = booleanFlag(parsed.values, "json");
  const body = wantJson
    ? JSON.stringify(report, null, 2)
    : renderTraceFindingReportMarkdown(report);
  return ok(body);
}

function ok(stdout: string): TraceFindingsCommandResult {
  return { stdout, stderr: "", exitCode: 0 };
}

function fail(stderr: string, exitCode = 2): TraceFindingsCommandResult {
  return { stdout: "", stderr, exitCode };
}

function stringFlag(values: Record<string, unknown>, name: string): string | undefined {
  const value = values[name];
  return typeof value === "string" ? value : undefined;
}

function booleanFlag(values: Record<string, unknown>, name: string): boolean {
  return values[name] === true;
}

function messageOf(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
