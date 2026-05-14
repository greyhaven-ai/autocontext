/**
 * AC-679 trace-findings CLI workflow.
 *
 * Loads a trace and emits a TraceFindingReport. Two input modes:
 *
 * - `--trace <path>`  : read a PublicTrace JSON file directly (slice 2).
 * - `--trace-id <id>` : look up a stored ProductionTrace in the local
 *                       production-traces store and adapt it to PublicTrace
 *                       before running the extractor (slice 3b).
 *
 * The handler is pure -- returns `{stdout, stderr, exitCode}` instead of
 * writing to process streams -- so unit tests drive it directly without
 * subprocess spawn or stdout capture.
 */

import { readFile, stat } from "node:fs/promises";
import { parseArgs, type ParseArgsConfig } from "node:util";

import {
  PublicTraceSchema,
  generateTraceFindingReport,
  renderTraceFindingReportMarkdown,
} from "../index.js";
import type { PublicTrace } from "../traces/public-schema-contracts.js";
import type { ProductionTrace } from "../production-traces/contract/types.js";

export interface TraceFindingsCommandResult {
  readonly stdout: string;
  readonly stderr: string;
  readonly exitCode: number;
}

export interface TraceFindingsCommandContext {
  readonly cwd?: string;
}

export const TRACE_FINDINGS_HELP_TEXT = `autoctx trace-findings — extract structured findings from a trace (AC-679)

Usage:
  autoctx trace-findings --trace <path> [--json]
  autoctx trace-findings --trace-id <id> [--json]
  autoctx trace-findings --help

Options:
  --trace <path>     Path to a PublicTrace JSON file
  --trace-id <id>    Look up a stored ProductionTrace from the local
                     .autocontext/production-traces/ingested/ store
  --json             Emit the TraceFindingReport as JSON instead of Markdown
  -h, --help         Show this help

Exactly one of --trace and --trace-id is required.

Output:
  Default: Markdown report (sections: Summary, Findings, Failure Motifs)
  --json:  TraceFindingReport JSON matching TraceFindingReportSchema`;

const PARSE_OPTIONS: ParseArgsConfig["options"] = {
  trace: { type: "string" },
  "trace-id": { type: "string" },
  json: { type: "boolean" },
  help: { type: "boolean", short: "h" },
};

export async function runTraceFindingsCommand(
  args: readonly string[],
  context: TraceFindingsCommandContext = {},
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
  const traceId = stringFlag(parsed.values, "trace-id");
  const wantJson = booleanFlag(parsed.values, "json");

  if (tracePath && traceId) {
    return fail(
      "autoctx trace-findings: --trace and --trace-id are mutually exclusive; pass exactly one",
    );
  }
  if (!tracePath && !traceId) {
    return fail("autoctx trace-findings: one of --trace <path> or --trace-id <id> is required");
  }

  let publicTrace: PublicTrace;
  if (tracePath) {
    const loaded = await loadPublicTraceFromPath(tracePath);
    if ("error" in loaded) return fail(loaded.error);
    publicTrace = loaded.trace;
  } else {
    const loaded = await loadPublicTraceFromStore(traceId!, context.cwd ?? process.cwd());
    if ("error" in loaded) return fail(loaded.error);
    publicTrace = loaded.trace;
  }

  const report = generateTraceFindingReport(publicTrace);
  const body = wantJson
    ? JSON.stringify(report, null, 2)
    : renderTraceFindingReportMarkdown(report);
  return ok(body);
}

interface LoadOk {
  readonly trace: PublicTrace;
}
interface LoadErr {
  readonly error: string;
}
type LoadResult = LoadOk | LoadErr;

async function loadPublicTraceFromPath(tracePath: string): Promise<LoadResult> {
  try {
    const stats = await stat(tracePath);
    if (!stats.isFile()) {
      return { error: `autoctx trace-findings: --trace path is not a file: ${tracePath}` };
    }
  } catch (err) {
    return {
      error: `autoctx trace-findings: could not read trace file ${tracePath}: ${messageOf(err)}`,
    };
  }
  let raw: string;
  try {
    raw = await readFile(tracePath, "utf8");
  } catch (err) {
    return {
      error: `autoctx trace-findings: could not read trace file ${tracePath}: ${messageOf(err)}`,
    };
  }
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (err) {
    return {
      error: `autoctx trace-findings: could not parse JSON from ${tracePath}: ${messageOf(err)}`,
    };
  }
  const parsed = PublicTraceSchema.safeParse(data);
  if (!parsed.success) {
    const issues = parsed.error.issues
      .map((issue) => `${issue.path.join(".") || "<root>"}: ${issue.message}`)
      .join("; ");
    return {
      error: `autoctx trace-findings: file is not a valid PublicTrace: ${issues}`,
    };
  }
  return { trace: parsed.data };
}

async function loadPublicTraceFromStore(traceId: string, cwd: string): Promise<LoadResult> {
  let findTraceById: (cwd: string, id: string) => ProductionTrace | null;
  try {
    ({ findTraceById } = await import("../production-traces/cli/_shared/trace-loading.js"));
  } catch (err) {
    return {
      error: `autoctx trace-findings: could not load production-traces helper: ${messageOf(err)}`,
    };
  }

  let production: ProductionTrace | null;
  try {
    production = findTraceById(cwd, traceId);
  } catch (err) {
    return {
      error: `autoctx trace-findings: could not search production-traces store: ${messageOf(err)}`,
    };
  }
  if (production === null) {
    return {
      error: `autoctx trace-findings: trace id ${JSON.stringify(traceId)} not found in ${cwd}/.autocontext/production-traces/ingested`,
    };
  }
  return { trace: productionTraceToPublicTrace(production) };
}

/**
 * Adapt a ProductionTrace to a PublicTrace for finding extraction.
 *
 * The structural shapes overlap: both have `traceId`, `messages` (with
 * embedded `toolCalls`), and an `outcome`. The mapping flattens
 * `source.emitter` to `sourceHarness`, derives `collectedAt` from
 * `timing.startedAt`, and only populates `outcome` when ProductionTrace
 * supplies both `score` and `reasoning` (PublicTrace requires both).
 */
function productionTraceToPublicTrace(trace: ProductionTrace): PublicTrace {
  const publicTrace: PublicTrace = {
    schemaVersion: "1.0.0",
    traceId: trace.traceId,
    sourceHarness: trace.source.emitter,
    collectedAt: trace.timing.startedAt,
    messages: trace.messages.map((m) => ({
      role: m.role,
      content: m.content,
      timestamp: m.timestamp,
      ...(m.toolCalls !== undefined ? { toolCalls: [...m.toolCalls] } : {}),
      ...(m.metadata !== undefined ? { metadata: m.metadata } : {}),
    })),
  };

  if (trace.session?.requestId !== undefined) {
    (publicTrace as { sessionId?: string }).sessionId = trace.session.requestId;
  }

  if (
    trace.outcome &&
    typeof trace.outcome.score === "number" &&
    typeof trace.outcome.reasoning === "string"
  ) {
    (publicTrace as { outcome?: PublicTrace["outcome"] }).outcome = {
      score: trace.outcome.score,
      reasoning: trace.outcome.reasoning,
      dimensions: trace.outcome.signals ?? {},
    };
  }

  return publicTrace;
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
