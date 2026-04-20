// Shared integration-test fixtures for Foundation A Layer 9 flows 1-6.
//
// DRY: every helper below wraps an existing public API of Layers 1-8. No new
// production code lives here — these fixtures are pure test-tree plumbing.
//
// DDD: helper names mirror the spec's verbs (seed, build, make) rather than
// inventing new vocabulary. Every helper takes the working-directory `cwd` as
// an explicit parameter so tests own tmpdir lifecycle.
//
// Conventions
//   - Tmp directories are owned by the caller (each test mkdtempSync's its
//     own root + rmSync on teardown).
//   - Timestamps are explicit (no `Date.now()` leakage). Deterministic ULIDs
//     are minted via a seeded counter so flows are reproducible run-to-run.
//   - The helpers DO NOT write outside the supplied `cwd`.
//
// Layer 1-8 surfaces used:
//   - production-traces/contract/factories.ts                (createProductionTrace)
//   - production-traces/contract/branded-ids.ts              (parseProductionTraceId, parseScenario)
//   - production-traces/ingest/paths.ts                      (incomingDir, ingestedDir)
//   - production-traces/redaction/index.ts                   (save*, defaults, initializeInstallSalt)
//   - production-traces/retention/index.ts                   (saveRetentionPolicy, defaultRetentionPolicy)
//   - production-traces/dataset/types.ts                     (Rubric, RubricLookup)

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  parseProductionTraceId,
  parseScenario,
  type AppId,
  type EnvironmentTag,
  type ProductionTraceId,
  type Scenario,
} from "../../../../../src/production-traces/contract/branded-ids.js";
import { createProductionTrace } from "../../../../../src/production-traces/contract/factories.js";
import type { ProductionTrace } from "../../../../../src/production-traces/contract/types.js";
import {
  incomingDir,
  ingestedDir,
} from "../../../../../src/production-traces/ingest/paths.js";
import {
  defaultRedactionPolicy,
  saveRedactionPolicy,
  initializeInstallSalt,
  type LoadedRedactionPolicy,
} from "../../../../../src/production-traces/redaction/index.js";
import {
  defaultRetentionPolicy,
  saveRetentionPolicy,
  type LoadedRetentionPolicy,
} from "../../../../../src/production-traces/retention/index.js";
import type {
  Rubric,
  RubricLookup,
} from "../../../../../src/production-traces/dataset/types.js";

// ----------------------------------------------------------------------------
// Deterministic ULID generation (scoped to one test-call so flows are stable)
// ----------------------------------------------------------------------------

/**
 * Build a deterministic ULID from a 4-digit suffix. The prefix mirrors the
 * shape used in `dataset/_helpers/fixtures.ts` so the two test tiers produce
 * comparable IDs. The suffix is uppercased Crockford base32 (0-9A-HJKMNP-TV-Z).
 *
 * Crucially: these ULIDs are LEXICOGRAPHICALLY ordered, so sorting by
 * traceId is equivalent to insertion order — convenient for asserting on
 * dataset rows.
 */
export function deterministicTraceId(index: number): ProductionTraceId {
  const suffix = index.toString(16).toUpperCase().padStart(4, "0");
  const raw = `01K000000000000000000A${suffix}`.slice(0, 26);
  const parsed = parseProductionTraceId(raw);
  if (parsed === null) {
    throw new Error(`deterministicTraceId(${index}) produced invalid ULID: ${raw}`);
  }
  return parsed;
}

// ----------------------------------------------------------------------------
// aProductionTrace — valid-minimal test-fixture builder
// ----------------------------------------------------------------------------

export interface TraceOverrides {
  readonly traceId?: ProductionTraceId;
  readonly startedAt?: string;
  readonly endedAt?: string;
  readonly taskType?: string;
  readonly appId?: string;
  readonly environmentTag?: string;
  readonly provider?: string;
  readonly model?: string;
  readonly messages?: ProductionTrace["messages"];
  readonly toolCalls?: ProductionTrace["toolCalls"];
  readonly outcome?: ProductionTrace["outcome"];
  readonly scenarioId?: string;
  readonly metadata?: Readonly<Record<string, unknown>>;
}

/**
 * Build a ProductionTrace that satisfies every §4 invariant. Overrides merge
 * on top of a passing-default shape. The factory (Layer 1) enforces schema +
 * branded-id discipline, so invalid overrides fail fast at construction.
 */
export function aProductionTrace(overrides: TraceOverrides = {}): ProductionTrace {
  const startedAt = overrides.startedAt ?? "2026-04-17T12:00:00.000Z";
  const endedAt = overrides.endedAt ?? isoOffset(startedAt, 1);
  const scenarioId =
    overrides.scenarioId !== undefined ? parseScenario(overrides.scenarioId) : null;
  if (overrides.scenarioId !== undefined && scenarioId === null) {
    throw new Error(`aProductionTrace: invalid scenarioId ${overrides.scenarioId}`);
  }
  return createProductionTrace({
    id: overrides.traceId ?? undefined,
    source: { emitter: "sdk", sdk: { name: "autoctx-ts", version: "0.4.3" } },
    provider: { name: overrides.provider ?? "openai" },
    model: overrides.model ?? "gpt-4o-mini",
    env: {
      environmentTag: (overrides.environmentTag ?? "production") as EnvironmentTag,
      appId: (overrides.appId ?? "my-app") as AppId,
      ...(overrides.taskType !== undefined ? { taskType: overrides.taskType } : {}),
    },
    messages:
      overrides.messages ??
      [{ role: "user", content: "hello", timestamp: startedAt }],
    toolCalls: overrides.toolCalls ?? [],
    ...(overrides.outcome !== undefined ? { outcome: overrides.outcome } : {}),
    timing: { startedAt, endedAt, latencyMs: secondsBetween(startedAt, endedAt) * 1000 },
    usage: { tokensIn: 10, tokensOut: 5 },
    links: scenarioId ? { scenarioId: scenarioId as Scenario } : {},
    ...(overrides.metadata !== undefined ? { metadata: overrides.metadata } : {}),
  });
}

// ----------------------------------------------------------------------------
// seedTracesInRegistry — drop a batch into incoming/ for an ingest test
// ----------------------------------------------------------------------------

export interface SeedTracesOptions {
  readonly traces: readonly ProductionTrace[];
  readonly batchId?: string;
  readonly date?: string;
  /** Additionally scaffold + initialize the install salt + default policies. */
  readonly installSalt?: boolean;
  /** Override the redaction policy written during scaffold. */
  readonly redactionPolicy?: LoadedRedactionPolicy;
  /** Override the retention policy written during scaffold. */
  readonly retentionPolicy?: LoadedRetentionPolicy;
}

/**
 * Write a JSONL batch into `.autocontext/production-traces/incoming/<date>/`
 * suitable for a subsequent `ingest` run. Optionally scaffolds the policy
 * files + install-salt (the default for most integration flows).
 *
 * Returns the absolute path of the written batch file.
 */
export async function seedTracesInRegistry(
  cwd: string,
  opts: SeedTracesOptions,
): Promise<string> {
  const date = opts.date ?? isoDate(opts.traces[0]?.timing.startedAt ?? "2026-04-17T12:00:00.000Z");
  const batchId = opts.batchId ?? "batch-seed";
  const dir = incomingDir(cwd, date);
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${batchId}.jsonl`);
  const body =
    opts.traces.length === 0
      ? ""
      : opts.traces.map((t) => JSON.stringify(t)).join("\n") + "\n";
  writeFileSync(path, body, "utf-8");

  if (opts.installSalt === true) {
    await initializeInstallSalt(cwd);
    await saveRedactionPolicy(cwd, opts.redactionPolicy ?? defaultRedactionPolicy());
    await saveRetentionPolicy(cwd, opts.retentionPolicy ?? defaultRetentionPolicy());
  } else {
    if (opts.redactionPolicy !== undefined) {
      await saveRedactionPolicy(cwd, opts.redactionPolicy);
    }
    if (opts.retentionPolicy !== undefined) {
      await saveRetentionPolicy(cwd, opts.retentionPolicy);
    }
  }

  return path;
}

// ----------------------------------------------------------------------------
// seedIngestedTraces — skip ingest, place traces directly in ingested/
// ----------------------------------------------------------------------------

export interface SeedIngestedOptions {
  readonly traces: readonly ProductionTrace[];
  readonly batchId?: string;
  readonly date?: string;
  readonly retentionPolicy?: LoadedRetentionPolicy;
}

/**
 * Write a JSONL batch directly into `ingested/<date>/`, bypassing ingest.
 * Used by retention/export tests that start from an already-ingested state.
 *
 * Returns the absolute path of the written batch file.
 */
export async function seedIngestedTraces(
  cwd: string,
  opts: SeedIngestedOptions,
): Promise<string> {
  const date = opts.date ?? isoDate(opts.traces[0]?.timing.startedAt ?? "2026-04-17T12:00:00.000Z");
  const batchId = opts.batchId ?? "batch-ingested";
  const dir = ingestedDir(cwd, date);
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${batchId}.jsonl`);
  const body =
    opts.traces.length === 0
      ? ""
      : opts.traces.map((t) => JSON.stringify(t)).join("\n") + "\n";
  writeFileSync(path, body, "utf-8");

  if (opts.retentionPolicy !== undefined) {
    await saveRetentionPolicy(cwd, opts.retentionPolicy);
  }
  return path;
}

// ----------------------------------------------------------------------------
// buildPolicyFile / buildRetentionPolicy — merge helpers over defaults
// ----------------------------------------------------------------------------

/**
 * Merge a partial redaction policy on top of defaultRedactionPolicy() and
 * save it to disk as canonical JSON.
 */
export async function buildPolicyFile(
  cwd: string,
  partial: Partial<LoadedRedactionPolicy>,
): Promise<void> {
  const base = defaultRedactionPolicy();
  const merged: LoadedRedactionPolicy = {
    ...base,
    ...partial,
    autoDetect: { ...base.autoDetect, ...(partial.autoDetect ?? {}) },
    exportPolicy: { ...base.exportPolicy, ...(partial.exportPolicy ?? {}) },
  };
  await saveRedactionPolicy(cwd, merged);
}

/**
 * Merge a partial retention policy on top of defaultRetentionPolicy() and
 * save it to disk.
 */
export async function buildRetentionPolicy(
  cwd: string,
  partial: Partial<LoadedRetentionPolicy>,
): Promise<void> {
  const base = defaultRetentionPolicy();
  const merged: LoadedRetentionPolicy = { ...base, ...partial };
  await saveRetentionPolicy(cwd, merged);
}

// ----------------------------------------------------------------------------
// aMockRubricLookup — test-only RubricLookup wired for build-dataset flows
// ----------------------------------------------------------------------------

/**
 * Build a RubricLookup that returns `rubricsByScenario[scenarioId]` or null.
 *
 * When the caller passes nothing, the lookup unconditionally returns null —
 * exercising the "no registry match" branch of the precedence ladder (§8.3).
 */
export function aMockRubricLookup(
  rubricsByScenario: Readonly<Record<string, Rubric>> = {},
): RubricLookup {
  return async (scenarioId) => rubricsByScenario[scenarioId as string] ?? null;
}

// ----------------------------------------------------------------------------
// Internals
// ----------------------------------------------------------------------------

function isoOffset(iso: string, addSeconds: number): string {
  const d = new Date(iso);
  d.setSeconds(d.getSeconds() + addSeconds);
  return d.toISOString();
}

function secondsBetween(a: string, b: string): number {
  return (Date.parse(b) - Date.parse(a)) / 1000;
}

function isoDate(iso: string): string {
  return iso.slice(0, 10);
}
