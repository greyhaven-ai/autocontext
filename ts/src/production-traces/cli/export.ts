// `autoctx production-traces export ...`
//
// Exports traces to an outbound format, applying redaction at the export
// boundary (spec §7.5 — "Boundary = leaves the installation's filesystem").
//
// Supported formats for v1: `public-trace` (the canonical JSON-per-trace
// outbound schema) and `jsonl` (one JSON trace per line, convenient for
// operator consumption). `parquet` is deferred to a later hosted path — the
// CLI recognizes the flag but errors out clearly.

import {
  existsSync,
  mkdirSync,
  writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, resolve as pathResolve } from "node:path";
import { loadIngestedTraces, type TraceFilter } from "./_shared/trace-loading.js";
import {
  loadRedactionPolicy,
  loadInstallSalt,
  applyRedactions,
} from "../redaction/index.js";
import type {
  CategoryAction,
  CategoryOverride,
  LoadedRedactionPolicy,
} from "../redaction/types.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import {
  parseFlags,
  stringFlag,
  stringArrayFlag,
  booleanFlag,
} from "./_shared/flags.js";
import type { CliContext, CliResult } from "./_shared/types.js";

export const EXPORT_HELP_TEXT = `autoctx production-traces export — emit traces with redaction applied

Usage:
  autoctx production-traces export --format public-trace|jsonl|parquet
      [--since <iso-ts>] [--until <iso-ts>] [--env <tag>]
      [--output-path <file>]
      [--include-raw-provider-payload]
      [--category-override <key=action>]...

Formats:
  public-trace   One canonical JSON document per trace (default choice).
  jsonl          One trace per line; ideal for piping.
  parquet        NOT IMPLEMENTED in v1. Use jsonl.

Redaction:
  Always applied at the export boundary per spec §7.5. Per-invocation category
  overrides take effect on top of the policy file's categoryOverrides:
      --category-override pii-email=hash
      --category-override secret-token=drop
  Valid actions: redact, hash, preserve, drop.
`;

const VALID_FORMATS = new Set(["public-trace", "jsonl", "parquet"]);
const VALID_ACTIONS: readonly CategoryAction[] = ["redact", "hash", "preserve", "drop"];

export async function runExport(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  if (args[0] === "--help" || args[0] === "-h") {
    return { stdout: EXPORT_HELP_TEXT, stderr: "", exitCode: EXIT.SUCCESS };
  }
  const flags = parseFlags(args, {
    format: { type: "string", required: true },
    since: { type: "string" },
    until: { type: "string" },
    env: { type: "string" },
    "output-path": { type: "string" },
    "include-raw-provider-payload": { type: "boolean" },
    "category-override": { type: "string-array" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const format = stringFlag(flags.value, "format")!;
  if (!VALID_FORMATS.has(format)) {
    return {
      stdout: "",
      stderr: `invalid --format '${format}' (valid: public-trace, jsonl, parquet)`,
      exitCode: EXIT.DOMAIN_FAILURE,
    };
  }
  if (format === "parquet") {
    return {
      stdout: "",
      stderr: "format 'parquet' not implemented in v1; use jsonl",
      exitCode: EXIT.DOMAIN_FAILURE,
    };
  }

  const since = stringFlag(flags.value, "since");
  const until = stringFlag(flags.value, "until");
  const env = stringFlag(flags.value, "env");
  const outputPath = stringFlag(flags.value, "output-path");
  const includeRaw = booleanFlag(flags.value, "include-raw-provider-payload");
  const overrideTokens = stringArrayFlag(flags.value, "category-override");
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;

  // Parse --category-override tokens.
  const overrides: Record<string, CategoryOverride> = {};
  for (const tok of overrideTokens) {
    const eq = tok.indexOf("=");
    if (eq < 1) {
      return {
        stdout: "",
        stderr: `invalid --category-override '${tok}' (expected key=action)`,
        exitCode: EXIT.DOMAIN_FAILURE,
      };
    }
    const key = tok.slice(0, eq);
    const action = tok.slice(eq + 1) as CategoryAction;
    if (!VALID_ACTIONS.includes(action)) {
      return {
        stdout: "",
        stderr: `invalid --category-override action '${action}' (valid: ${VALID_ACTIONS.join(", ")})`,
        exitCode: EXIT.DOMAIN_FAILURE,
      };
    }
    overrides[key] = { action };
  }

  // Load and override policy.
  let basePolicy: LoadedRedactionPolicy;
  let salt: string | null;
  try {
    basePolicy = await loadRedactionPolicy(ctx.cwd);
    salt = await loadInstallSalt(ctx.cwd);
  } catch (err) {
    return { stdout: "", stderr: `policy: ${msgOf(err)}`, exitCode: EXIT.INVALID_CONFIG };
  }
  const effectivePolicy: LoadedRedactionPolicy = {
    ...basePolicy,
    exportPolicy: {
      ...basePolicy.exportPolicy,
      includeRawProviderPayload: includeRaw || basePolicy.exportPolicy.includeRawProviderPayload,
      categoryOverrides: {
        ...basePolicy.exportPolicy.categoryOverrides,
        ...overrides,
      },
    },
  };

  // Load traces.
  const filter: TraceFilter = {
    ...(since !== undefined ? { since } : {}),
    ...(until !== undefined ? { until } : {}),
    ...(env !== undefined ? { env } : {}),
  };
  let traces;
  try {
    traces = loadIngestedTraces(ctx.cwd, filter);
  } catch (err) {
    return { stdout: "", stderr: `load traces: ${msgOf(err)}`, exitCode: EXIT.IO_FAILURE };
  }
  if (traces.length === 0) {
    return {
      stdout: "",
      stderr: `no ingested traces match filter`,
      exitCode: EXIT.NO_MATCHING_TRACES,
    };
  }

  // Apply redaction at export boundary.
  const redacted = traces.map((t) => applyRedactions(t, effectivePolicy, salt));

  // Serialize per format.
  let body: string;
  if (format === "jsonl") {
    body = redacted.map((t) => JSON.stringify(t)).join("\n") + "\n";
  } else {
    // public-trace — JSON array of traces (single document). Public-trace
    // in this v1 is simply the ProductionTrace shape with redactions applied;
    // future schema versions may narrow this further.
    body = JSON.stringify(redacted);
  }

  // Destination: --output-path writes to disk; absent writes to stdout.
  if (outputPath !== undefined) {
    const resolved = isAbsolute(outputPath) ? outputPath : pathResolve(ctx.cwd, outputPath);
    try {
      mkdirSync(dirname(resolved), { recursive: true });
      writeFileSync(resolved, body, "utf-8");
    } catch (err) {
      return { stdout: "", stderr: `write to ${resolved}: ${msgOf(err)}`, exitCode: EXIT.IO_FAILURE };
    }
    const summary = {
      format,
      destination: resolved,
      tracesExported: redacted.length,
      redactionApplied: true,
    };
    return {
      stdout: formatOutput(summary, output),
      stderr: "",
      exitCode: EXIT.SUCCESS,
    };
  }

  // stdout: raw body (caller pipes to a file / jq).
  return {
    stdout: body,
    stderr: "",
    exitCode: EXIT.SUCCESS,
  };
}

function msgOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// Suppress "existsSync unused" lint false-positive (may be needed once tests
// start exercising the error paths on a pre-existing output file).
void existsSync;
