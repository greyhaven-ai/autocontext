// MCP tools for the production-traces namespace.
//
// Each tool is a thin wrapper around a `runProductionTracesCommand` invocation
// — matching Foundation B's convention of keeping CLI and MCP paths aligned.
// The MCP surface is deliberately verb-and-noun-explicit (spec §9.2):
//
//   production_traces_init
//   production_traces_ingest
//   production_traces_list
//   production_traces_show
//   production_traces_stats
//   production_traces_build_dataset
//   production_traces_datasets_list
//   production_traces_datasets_show
//   production_traces_export
//   production_traces_policy_show
//   production_traces_policy_set
//   production_traces_rotate_salt
//   production_traces_prune
//
// Return shape is the CliResult JSON ({ stdout, stderr, exitCode }) — the
// agent integrator can then parse stdout as JSON (we always pass --output json
// into the runner) or inspect stderr for advisory warnings.

import { z } from "zod";
import { runProductionTracesCommand } from "../production-traces/cli/index.js";

interface JsonToolResponse {
  content: Array<{ type: "text"; text: string }>;
}

type McpToolRegistrar = {
  tool: (...args: any[]) => unknown;
};

function jsonText(payload: unknown, indent?: number): JsonToolResponse {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, indent),
      },
    ],
  };
}

/**
 * Shared helper: invoke a subcommand with a supplied argv tail, forcing
 * `--output json` so the caller can reliably parse `stdout`.
 *
 * All production-traces commands accept `--output json`. A few (like the bare
 * `export` path that writes JSONL to stdout) deliberately do not — callers
 * of those tools get the raw text in `stdout`.
 */
async function runTool(
  argv: readonly string[],
  extraArgs: readonly string[] = [],
  opts: { readonly cwd?: string } = {},
): Promise<JsonToolResponse> {
  const full = [...argv, ...extraArgs];
  const res = await runProductionTracesCommand(full, opts.cwd ? { cwd: opts.cwd } : {});
  return jsonText({
    stdout: res.stdout,
    stderr: res.stderr,
    exitCode: res.exitCode,
  }, 2);
}

/**
 * Append `--output json` if not already present, so the returned `stdout` is
 * reliably JSON-parseable for the agent integrator.
 */
function withJsonOutput(argv: readonly string[]): readonly string[] {
  if (argv.includes("--output")) return argv;
  return [...argv, "--output", "json"];
}

export function registerProductionTracesTools(server: McpToolRegistrar): void {
  // init
  server.tool(
    "production_traces_init",
    "Scaffold .autocontext/production-traces/ and generate the install-salt. Idempotent.",
    { cwd: z.string().optional() },
    async (args: Record<string, unknown>) =>
      runTool(["init"], ["--output", "json"], { cwd: args.cwd as string | undefined }),
  );

  // ingest
  server.tool(
    "production_traces_ingest",
    "Validate and move incoming/ trace batches into ingested/. Acquires the shared .autocontext/lock.",
    {
      cwd: z.string().optional(),
      since: z.string().optional(),
      strict: z.boolean().optional(),
      dryRun: z.boolean().optional(),
    },
    async (args: Record<string, unknown>) => {
      const argv: string[] = ["ingest"];
      if (typeof args.since === "string") argv.push("--since", args.since);
      if (args.strict === true) argv.push("--strict");
      if (args.dryRun === true) argv.push("--dry-run");
      return runTool(withJsonOutput(argv), [], { cwd: args.cwd as string | undefined });
    },
  );

  // list
  server.tool(
    "production_traces_list",
    "List locally-stored traces (no redaction applied). Supports filters matching the CLI.",
    {
      cwd: z.string().optional(),
      since: z.string().optional(),
      until: z.string().optional(),
      env: z.string().optional(),
      app: z.string().optional(),
      provider: z.string().optional(),
      outcome: z.string().optional(),
      limit: z.number().int().positive().optional(),
    },
    async (args: Record<string, unknown>) => {
      const argv: string[] = ["list"];
      for (const k of ["since", "until", "env", "app", "provider", "outcome"] as const) {
        const v = args[k];
        if (typeof v === "string" && v.length > 0) argv.push(`--${k}`, v);
      }
      if (typeof args.limit === "number") argv.push("--limit", String(args.limit));
      return runTool(withJsonOutput(argv), [], { cwd: args.cwd as string | undefined });
    },
  );

  // show
  server.tool(
    "production_traces_show",
    "Inspect a single trace by traceId. Pass asExported to preview redaction at the export boundary.",
    {
      cwd: z.string().optional(),
      traceId: z.string(),
      asExported: z.boolean().optional(),
    },
    async (args: Record<string, unknown>) => {
      const argv: string[] = ["show", args.traceId as string];
      if (args.asExported === true) argv.push("--as-exported");
      return runTool(withJsonOutput(argv), [], { cwd: args.cwd as string | undefined });
    },
  );

  // stats
  server.tool(
    "production_traces_stats",
    "Aggregate counts across ingested traces; group by env | app | provider | outcome | cluster.",
    {
      cwd: z.string().optional(),
      since: z.string().optional(),
      until: z.string().optional(),
      by: z.enum(["env", "app", "provider", "outcome", "cluster"]).optional(),
    },
    async (args: Record<string, unknown>) => {
      const argv: string[] = ["stats"];
      if (typeof args.since === "string") argv.push("--since", args.since);
      if (typeof args.until === "string") argv.push("--until", args.until);
      if (typeof args.by === "string") argv.push("--by", args.by);
      return runTool(withJsonOutput(argv), [], { cwd: args.cwd as string | undefined });
    },
  );

  // build-dataset
  server.tool(
    "production_traces_build_dataset",
    "Build an evaluation dataset from curated traces (spec AC-541). Supports CLI filters and wires registry-backed RubricLookup.",
    {
      cwd: z.string().optional(),
      name: z.string(),
      config: z.string().optional(),
      since: z.string().optional(),
      until: z.string().optional(),
      provider: z.string().optional(),
      app: z.string().optional(),
      env: z.string().optional(),
      outcome: z.string().optional(),
      clusterStrategy: z.enum(["taskType", "rules"]).optional(),
      rules: z.string().optional(),
      rubrics: z.string().optional(),
      allowSyntheticRubrics: z.boolean().optional(),
      seed: z.number().int().optional(),
      newId: z.boolean().optional(),
    },
    async (args: Record<string, unknown>) => {
      const argv: string[] = ["build-dataset", "--name", args.name as string];
      if (typeof args.config === "string") argv.push("--config", args.config);
      if (typeof args.since === "string") argv.push("--since", args.since);
      if (typeof args.until === "string") argv.push("--until", args.until);
      for (const k of ["provider", "app", "env", "outcome"] as const) {
        const v = args[k];
        if (typeof v === "string" && v.length > 0) argv.push(`--${k}`, v);
      }
      if (typeof args.clusterStrategy === "string") argv.push("--cluster-strategy", args.clusterStrategy);
      if (typeof args.rules === "string") argv.push("--rules", args.rules);
      if (typeof args.rubrics === "string") argv.push("--rubrics", args.rubrics);
      if (args.allowSyntheticRubrics === true) argv.push("--allow-synthetic-rubrics");
      if (typeof args.seed === "number") argv.push("--seed", String(args.seed));
      if (args.newId === true) argv.push("--new-id");
      return runTool(withJsonOutput(argv), [], { cwd: args.cwd as string | undefined });
    },
  );

  // datasets list
  server.tool(
    "production_traces_datasets_list",
    "List dataset manifests under .autocontext/datasets/.",
    { cwd: z.string().optional() },
    async (args: Record<string, unknown>) =>
      runTool(["datasets", "list", "--output", "json"], [], { cwd: args.cwd as string | undefined }),
  );

  // datasets show
  server.tool(
    "production_traces_datasets_show",
    "Render a specific dataset's manifest.",
    { cwd: z.string().optional(), datasetId: z.string() },
    async (args: Record<string, unknown>) =>
      runTool(
        ["datasets", "show", args.datasetId as string, "--output", "json"],
        [],
        { cwd: args.cwd as string | undefined },
      ),
  );

  // export
  server.tool(
    "production_traces_export",
    "Emit traces with redaction applied at the export boundary.",
    {
      cwd: z.string().optional(),
      format: z.enum(["public-trace", "jsonl", "parquet"]),
      since: z.string().optional(),
      until: z.string().optional(),
      env: z.string().optional(),
      outputPath: z.string().optional(),
      includeRawProviderPayload: z.boolean().optional(),
      categoryOverride: z.array(z.string()).optional(),
    },
    async (args: Record<string, unknown>) => {
      const argv: string[] = ["export", "--format", args.format as string];
      if (typeof args.since === "string") argv.push("--since", args.since);
      if (typeof args.until === "string") argv.push("--until", args.until);
      if (typeof args.env === "string") argv.push("--env", args.env);
      if (typeof args.outputPath === "string") argv.push("--output-path", args.outputPath);
      if (args.includeRawProviderPayload === true) argv.push("--include-raw-provider-payload");
      const overrides = Array.isArray(args.categoryOverride)
        ? (args.categoryOverride as string[])
        : [];
      for (const o of overrides) {
        argv.push("--category-override", o);
      }
      return runTool(argv, [], { cwd: args.cwd as string | undefined });
    },
  );

  // policy show
  server.tool(
    "production_traces_policy_show",
    "Print the current redaction policy.",
    { cwd: z.string().optional() },
    async (args: Record<string, unknown>) =>
      runTool(["policy", "show", "--output", "json"], [], { cwd: args.cwd as string | undefined }),
  );

  // policy set
  server.tool(
    "production_traces_policy_set",
    "Change the redaction mode. on-ingest -> on-export requires force: true.",
    {
      cwd: z.string().optional(),
      mode: z.enum(["on-export", "on-ingest"]),
      force: z.boolean().optional(),
    },
    async (args: Record<string, unknown>) => {
      const argv: string[] = ["policy", "set", "--mode", args.mode as string];
      if (args.force === true) argv.push("--force");
      return runTool(withJsonOutput(argv), [], { cwd: args.cwd as string | undefined });
    },
  );

  // rotate-salt
  server.tool(
    "production_traces_rotate_salt",
    "Rotate the install-salt (break-glass). Requires force: true.",
    { cwd: z.string().optional(), force: z.boolean().optional() },
    async (args: Record<string, unknown>) => {
      const argv: string[] = ["rotate-salt"];
      if (args.force === true) argv.push("--force");
      return runTool(withJsonOutput(argv), [], { cwd: args.cwd as string | undefined });
    },
  );

  // prune
  server.tool(
    "production_traces_prune",
    "Enforce retention policy out-of-band.",
    { cwd: z.string().optional(), dryRun: z.boolean().optional() },
    async (args: Record<string, unknown>) => {
      const argv: string[] = ["prune"];
      if (args.dryRun === true) argv.push("--dry-run");
      return runTool(withJsonOutput(argv), [], { cwd: args.cwd as string | undefined });
    },
  );
}
