// `autoctx production-traces build-dataset ...`
//
// Loads source traces (filtered by --since/--until/--provider/--app/--env/--outcome),
// reads cluster + rubric configs, accepts an optional registry-backed RubricLookup
// from the composition root, and invokes Layer 5's `buildDataset(inputs)` orchestrator.
//
// Exit-code contract (spec §9.7):
//   0  dataset written successfully
//   1  domain failure (e.g. invalid cluster strategy, no traces after filter)
//   11 invalid config file
//   12 no matching traces
//   13 schema version mismatch (reading a newer unknown schema)
//   14 I/O failure

import { existsSync, readFileSync } from "node:fs";
import { resolve as pathResolve, isAbsolute } from "node:path";
import { buildDataset } from "../dataset/index.js";
import type {
  BuildDatasetInputs,
  BuildDatasetResult,
  ClusterConfig,
  ClusterStrategy,
  RubricConfig,
  SelectionRule,
} from "../dataset/index.js";
import { loadRedactionPolicy, loadInstallSalt } from "../redaction/index.js";
import { loadIngestedTraces, type TraceFilter } from "./_shared/trace-loading.js";
import { acquireLock } from "../ingest/lock.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import { parseFlags, stringFlag, booleanFlag } from "./_shared/flags.js";
import type { CliContext, CliResult } from "./_shared/types.js";

export const BUILD_DATASET_HELP_TEXT = `autoctx production-traces build-dataset — generate a dataset from traces

Usage:
  autoctx production-traces build-dataset --name <str>
      [--config ./dataset-config.json]
      [--since <iso-ts>] [--until <iso-ts>]
      [--provider <name>]
      [--app <app-id>]
      [--env <env-tag>]
      [--outcome <label>]
      [--cluster-strategy taskType|rules]
      [--rules ./cluster-config.json]
      [--rubrics ./rubric-config.json]
      [--allow-synthetic-rubrics]
      [--seed <N>]
      [--new-id]
      [--output json|pretty|table]

Behavior:
  1. Acquire .autocontext/lock (shared with Foundation B).
  2. Load ingested traces (filtered by --since/--until/--provider/--app/--env/--outcome).
  3. Optionally load cluster / rubric configs.
  4. Wire a registry-backed RubricLookup that resolves scenarioId via the
     control-plane artifact store. Returns null when no active artifact exists
     for the scenario, which falls through to synthetic or skip per §8.3.
  5. Invoke buildDataset() to cluster, select, split, redact, and write
     .autocontext/datasets/<datasetId>/.

Flags:
  --provider <name>    Filter traces by provider name (e.g. openai, anthropic).
  --app <app-id>       Filter traces by appId.
  --env <env-tag>      Filter traces by environmentTag.
  --outcome <label>    Filter traces by outcome label (success, failure, partial).

Exit codes:
  0  success
  1  domain failure
  11 invalid config
  12 no matching traces
  14 I/O failure
`;

export async function runBuildDataset(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  if (args[0] === "--help" || args[0] === "-h") {
    return { stdout: BUILD_DATASET_HELP_TEXT, stderr: "", exitCode: EXIT.SUCCESS };
  }
  const flags = parseFlags(args, {
    name: { type: "string", required: true },
    description: { type: "string" },
    config: { type: "string" },
    since: { type: "string" },
    until: { type: "string" },
    provider: { type: "string" },
    app: { type: "string" },
    env: { type: "string" },
    outcome: { type: "string" },
    "cluster-strategy": { type: "string", default: "taskType" },
    rules: { type: "string" },
    rubrics: { type: "string" },
    "allow-synthetic-rubrics": { type: "boolean" },
    seed: { type: "string", default: "42" },
    "new-id": { type: "boolean" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const name = stringFlag(flags.value, "name")!;
  const description = stringFlag(flags.value, "description") ?? "";
  const configPath = stringFlag(flags.value, "config");
  const since = stringFlag(flags.value, "since");
  const until = stringFlag(flags.value, "until");
  const provider = stringFlag(flags.value, "provider");
  const app = stringFlag(flags.value, "app");
  const env = stringFlag(flags.value, "env");
  const outcome = stringFlag(flags.value, "outcome");
  const clusterStrategyRaw = stringFlag(flags.value, "cluster-strategy") ?? "taskType";
  const rulesPath = stringFlag(flags.value, "rules");
  const rubricsPath = stringFlag(flags.value, "rubrics");
  const allowSynthetic = booleanFlag(flags.value, "allow-synthetic-rubrics");
  const seedRaw = stringFlag(flags.value, "seed") ?? "42";
  const newId = booleanFlag(flags.value, "new-id");
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;

  if (!(clusterStrategyRaw === "taskType" || clusterStrategyRaw === "rules")) {
    return {
      stdout: "",
      stderr: `invalid --cluster-strategy '${clusterStrategyRaw}' (expected taskType|rules)`,
      exitCode: EXIT.DOMAIN_FAILURE,
    };
  }
  const clusterStrategy = clusterStrategyRaw as ClusterStrategy;

  const seed = Number.parseInt(seedRaw, 10);
  if (!Number.isFinite(seed)) {
    return {
      stdout: "",
      stderr: `invalid --seed '${seedRaw}' (expected integer)`,
      exitCode: EXIT.DOMAIN_FAILURE,
    };
  }

  // --- Load optional config bundle -----------------------------------------
  // `--config` provides selectionRules + cluster/rubric defaults in one file.
  let configBundle: DatasetConfigBundle = {};
  if (configPath !== undefined) {
    const resolved = resolvePath(ctx.cwd, configPath);
    if (!existsSync(resolved)) {
      return {
        stdout: "",
        stderr: `--config file not found: ${resolved}`,
        exitCode: EXIT.INVALID_CONFIG,
      };
    }
    try {
      configBundle = JSON.parse(readFileSync(resolved, "utf-8")) as DatasetConfigBundle;
    } catch (err) {
      return {
        stdout: "",
        stderr: `--config malformed JSON at ${resolved}: ${msgOf(err)}`,
        exitCode: EXIT.INVALID_CONFIG,
      };
    }
  }

  // --- Load cluster rules (if --rules or inferred from --cluster-strategy) -
  let clusterConfig: ClusterConfig | undefined;
  if (rulesPath !== undefined) {
    const resolved = resolvePath(ctx.cwd, rulesPath);
    if (!existsSync(resolved)) {
      return {
        stdout: "",
        stderr: `--rules file not found: ${resolved}`,
        exitCode: EXIT.INVALID_CONFIG,
      };
    }
    try {
      clusterConfig = JSON.parse(readFileSync(resolved, "utf-8")) as ClusterConfig;
    } catch (err) {
      return {
        stdout: "",
        stderr: `--rules malformed JSON: ${msgOf(err)}`,
        exitCode: EXIT.INVALID_CONFIG,
      };
    }
  } else if (configBundle.clusterConfig !== undefined) {
    clusterConfig = configBundle.clusterConfig;
  }
  if (clusterStrategy === "rules" && clusterConfig === undefined) {
    return {
      stdout: "",
      stderr: "--cluster-strategy 'rules' requires --rules <path> or a clusterConfig in --config",
      exitCode: EXIT.DOMAIN_FAILURE,
    };
  }

  // --- Load rubric config --------------------------------------------------
  let rubricConfig: RubricConfig | undefined;
  if (rubricsPath !== undefined) {
    const resolved = resolvePath(ctx.cwd, rubricsPath);
    if (!existsSync(resolved)) {
      return {
        stdout: "",
        stderr: `--rubrics file not found: ${resolved}`,
        exitCode: EXIT.INVALID_CONFIG,
      };
    }
    try {
      rubricConfig = JSON.parse(readFileSync(resolved, "utf-8")) as RubricConfig;
    } catch (err) {
      return {
        stdout: "",
        stderr: `--rubrics malformed JSON: ${msgOf(err)}`,
        exitCode: EXIT.INVALID_CONFIG,
      };
    }
  } else if (configBundle.rubricConfig !== undefined) {
    rubricConfig = configBundle.rubricConfig;
  }

  // --- Load traces ---------------------------------------------------------
  const filter: TraceFilter = {
    ...(since !== undefined ? { since } : {}),
    ...(until !== undefined ? { until } : {}),
    ...(provider !== undefined ? { provider } : {}),
    ...(app !== undefined ? { app } : {}),
    ...(env !== undefined ? { env } : {}),
    ...(outcome !== undefined ? { outcome } : {}),
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
      stderr: `no ingested traces match filter (since=${since ?? "-"}, until=${until ?? "-"}, provider=${provider ?? "-"}, app=${app ?? "-"}, env=${env ?? "-"}, outcome=${outcome ?? "-"})`,
      exitCode: EXIT.NO_MATCHING_TRACES,
    };
  }

  // --- Load redaction policy + install salt for export-boundary application
  let policy, salt;
  try {
    policy = await loadRedactionPolicy(ctx.cwd);
    salt = await loadInstallSalt(ctx.cwd);
  } catch (err) {
    return { stdout: "", stderr: `policy: ${msgOf(err)}`, exitCode: EXIT.INVALID_CONFIG };
  }

  // --- Resolve optional registry-backed RubricLookup ----------------------
  // The top-level CLI/MCP composition roots inject the control-plane adapter.
  // The production-traces domain remains usable on its own without importing
  // control-plane implementation modules.
  const rubricLookup = ctx.rubricLookup ?? null;

  // --- Invoke dataset pipeline under lock ---------------------------------
  let lock;
  try {
    lock = acquireLock(ctx.cwd);
  } catch (err) {
    return {
      stdout: "",
      stderr: `build-dataset: lock timeout: ${msgOf(err)}`,
      exitCode: EXIT.LOCK_TIMEOUT,
    };
  }
  let result: BuildDatasetResult;
  try {
    const selectionRules = (configBundle.selectionRules as SelectionRule[] | undefined) ?? [];
    const inputs: BuildDatasetInputs = {
      cwd: ctx.cwd,
      name,
      description,
      traces,
      clusterStrategy,
      ...(clusterConfig !== undefined ? { clusterConfig } : {}),
      selectionRules,
      ...(rubricConfig !== undefined ? { rubricConfig } : {}),
      ...(rubricLookup !== null ? { rubricLookup } : {}),
      allowSyntheticRubrics: allowSynthetic,
      redactionPolicy: policy,
      installSalt: salt,
      seed,
      newId,
      autoctxVersion: configBundle.autoctxVersion ?? "layer7",
    };
    result = await buildDataset(inputs);
  } catch (err) {
    return { stdout: "", stderr: `build-dataset: ${msgOf(err)}`, exitCode: EXIT.DOMAIN_FAILURE };
  } finally {
    lock.release();
  }

  // Render a compact summary by default; --output json returns the full result.
  if (output === "json") {
    return { stdout: formatOutput(result, "json"), stderr: "", exitCode: EXIT.SUCCESS };
  }
  const summary = {
    datasetId: result.datasetId,
    writePath: result.writePath,
    traceCount: result.stats.traceCount,
    clusterCount: result.stats.clusterCount,
    clustersSkipped: result.stats.clustersSkipped,
    splitSizes: result.stats.splitSizes,
  };
  return {
    stdout: formatOutput(summary, output),
    stderr: "",
    exitCode: EXIT.SUCCESS,
  };
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

interface DatasetConfigBundle {
  readonly selectionRules?: readonly unknown[];
  readonly clusterConfig?: ClusterConfig;
  readonly rubricConfig?: RubricConfig;
  readonly autoctxVersion?: string;
}

function resolvePath(cwd: string, p: string): string {
  return isAbsolute(p) ? p : pathResolve(cwd, p);
}

function msgOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
