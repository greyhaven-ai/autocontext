// `autoctx production-traces list | show | stats`
//
// Local-view commands — spec §7.5 says NO redaction is applied here unless
// `show --as-exported` is passed. These commands read from
// `.autocontext/production-traces/ingested/<date>/*.jsonl` and render.

import { loadIngestedTraces, findTraceById, type TraceFilter } from "./_shared/trace-loading.js";
import {
  loadRedactionPolicy,
  loadInstallSalt,
  applyRedactions,
} from "../redaction/index.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import { parseFlags, stringFlag, booleanFlag } from "./_shared/flags.js";
import type { ProductionTrace } from "../contract/types.js";
import type { CliContext, CliResult } from "./_shared/types.js";

export const LIST_HELP_TEXT = `autoctx production-traces list — list locally-stored traces (no redaction)

Usage:
  autoctx production-traces list
      [--since <iso-ts>] [--until <iso-ts>]
      [--env <tag>] [--app <id>] [--provider <name>] [--outcome <label>]
      [--limit <N>] [--output json|pretty|table]
`;

export const SHOW_HELP_TEXT = `autoctx production-traces show — inspect a single trace

Usage:
  autoctx production-traces show <traceId> [--as-exported] [--output json|pretty]

Behavior:
  Default renders the trace as stored locally (includes plaintext values under
  redaction markers). Pass --as-exported to preview what a customer-boundary
  export would look like (applies redaction per policy).
`;

export const STATS_HELP_TEXT = `autoctx production-traces stats — aggregate counts across ingested traces

Usage:
  autoctx production-traces stats
      [--since <iso-ts>] [--until <iso-ts>]
      [--by env|app|provider|outcome|cluster]
      [--output json|pretty|table]

Note:
  --by cluster groups by env.taskType — Tier-1 clustering per spec §8.1.
`;

// ----------------------------------------------------------------------------
// list
// ----------------------------------------------------------------------------

export async function runList(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  if (args[0] === "--help" || args[0] === "-h") {
    return { stdout: LIST_HELP_TEXT, stderr: "", exitCode: EXIT.SUCCESS };
  }
  const flags = parseFlags(args, {
    since: { type: "string" },
    until: { type: "string" },
    env: { type: "string" },
    app: { type: "string" },
    provider: { type: "string" },
    outcome: { type: "string" },
    limit: { type: "string" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;

  let filter: TraceFilter;
  try {
    filter = buildFilter(flags.value);
  } catch (err) {
    return { stdout: "", stderr: msgOf(err), exitCode: EXIT.DOMAIN_FAILURE };
  }

  let traces: ProductionTrace[];
  try {
    traces = loadIngestedTraces(ctx.cwd, filter);
  } catch (err) {
    return { stdout: "", stderr: msgOf(err), exitCode: EXIT.IO_FAILURE };
  }

  const rows = traces.map((t) => ({
    traceId: t.traceId,
    startedAt: t.timing.startedAt,
    env: t.env.environmentTag,
    app: t.env.appId,
    provider: t.provider.name,
    taskType: t.env.taskType ?? "",
    outcome: t.outcome?.label ?? "",
    score: t.outcome?.score ?? "",
  }));

  return {
    stdout: formatOutput(rows, output),
    stderr: "",
    exitCode: EXIT.SUCCESS,
  };
}

// ----------------------------------------------------------------------------
// show
// ----------------------------------------------------------------------------

export async function runShow(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const id = args[0];
  if (!id || id === "--help" || id === "-h") {
    return { stdout: SHOW_HELP_TEXT, stderr: "", exitCode: EXIT.SUCCESS };
  }
  const flags = parseFlags(args.slice(1), {
    "as-exported": { type: "boolean" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;
  const asExported = booleanFlag(flags.value, "as-exported");

  let trace: ProductionTrace | null;
  try {
    trace = findTraceById(ctx.cwd, id);
  } catch (err) {
    return { stdout: "", stderr: msgOf(err), exitCode: EXIT.IO_FAILURE };
  }
  if (trace === null) {
    return {
      stdout: "",
      stderr: `trace not found: ${id}`,
      exitCode: EXIT.NO_MATCHING_TRACES,
    };
  }

  let rendered = trace;
  if (asExported) {
    try {
      const policy = await loadRedactionPolicy(ctx.cwd);
      const salt = await loadInstallSalt(ctx.cwd);
      rendered = applyRedactions(trace, policy, salt);
    } catch (err) {
      return {
        stdout: "",
        stderr: `show --as-exported: ${msgOf(err)}`,
        exitCode: EXIT.INVALID_CONFIG,
      };
    }
  }

  return {
    stdout: formatOutput(rendered, output),
    stderr: "",
    exitCode: EXIT.SUCCESS,
  };
}

// ----------------------------------------------------------------------------
// stats
// ----------------------------------------------------------------------------

type StatsBy = "env" | "app" | "provider" | "outcome" | "cluster";
const STATS_BY: readonly StatsBy[] = ["env", "app", "provider", "outcome", "cluster"];

export async function runStats(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  if (args[0] === "--help" || args[0] === "-h") {
    return { stdout: STATS_HELP_TEXT, stderr: "", exitCode: EXIT.SUCCESS };
  }
  const flags = parseFlags(args, {
    since: { type: "string" },
    until: { type: "string" },
    by: { type: "string", default: "env" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const by = (stringFlag(flags.value, "by") ?? "env") as StatsBy;
  if (!STATS_BY.includes(by)) {
    return {
      stdout: "",
      stderr: `invalid --by '${by}'; valid: ${STATS_BY.join(", ")}`,
      exitCode: EXIT.DOMAIN_FAILURE,
    };
  }
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;

  let filter: TraceFilter;
  try {
    filter = buildFilter(flags.value);
  } catch (err) {
    return { stdout: "", stderr: msgOf(err), exitCode: EXIT.DOMAIN_FAILURE };
  }

  let traces: ProductionTrace[];
  try {
    traces = loadIngestedTraces(ctx.cwd, filter);
  } catch (err) {
    return { stdout: "", stderr: msgOf(err), exitCode: EXIT.IO_FAILURE };
  }

  const counts = new Map<string, number>();
  for (const t of traces) {
    const key = extractStatsKey(t, by);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const rows = Array.from(counts.entries())
    .sort((a, b) => {
      if (a[1] !== b[1]) return b[1] - a[1];
      return a[0].localeCompare(b[0]);
    })
    .map(([key, count]) => ({ [by]: key, count }));

  return {
    stdout: formatOutput(rows, output),
    stderr: "",
    exitCode: EXIT.SUCCESS,
  };
}

function extractStatsKey(t: ProductionTrace, by: StatsBy): string {
  switch (by) {
    case "env": return t.env.environmentTag;
    case "app": return t.env.appId;
    case "provider": return t.provider.name;
    case "outcome": return t.outcome?.label ?? "(unlabeled)";
    case "cluster": return t.env.taskType ?? "(uncategorized)";
  }
}

// ----------------------------------------------------------------------------
// shared
// ----------------------------------------------------------------------------

function buildFilter(flags: Record<string, unknown>): TraceFilter {
  const since = typeof flags.since === "string" ? flags.since : undefined;
  const until = typeof flags.until === "string" ? flags.until : undefined;
  const env = typeof flags.env === "string" ? flags.env : undefined;
  const app = typeof flags.app === "string" ? flags.app : undefined;
  const provider = typeof flags.provider === "string" ? flags.provider : undefined;
  const outcome = typeof flags.outcome === "string" ? flags.outcome : undefined;
  const limitRaw = typeof flags.limit === "string" ? flags.limit : undefined;
  let limit: number | undefined;
  if (limitRaw !== undefined) {
    limit = Number.parseInt(limitRaw, 10);
    if (!Number.isFinite(limit) || limit <= 0) {
      throw new Error(`--limit must be a positive integer (got: ${limitRaw})`);
    }
  }
  const f: TraceFilter = {
    ...(since !== undefined ? { since } : {}),
    ...(until !== undefined ? { until } : {}),
    ...(env !== undefined ? { env } : {}),
    ...(app !== undefined ? { app } : {}),
    ...(provider !== undefined ? { provider } : {}),
    ...(outcome !== undefined ? { outcome } : {}),
    ...(limit !== undefined ? { limit } : {}),
  };
  return f;
}

function msgOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
