// Shared fixtures for production-traces CLI tests.
//
// Each CLI test spins up a fresh tmpdir as cwd, drops a canned trace batch
// into incoming/<date>/, runs the in-process runner, and asserts on the
// CliResult. No subprocess spawning — mirrors the Foundation B CLI test
// pattern for speed.

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  newProductionTraceId,
  type ProductionTraceId,
} from "../../../../../src/production-traces/contract/branded-ids.js";
import type { ProductionTrace } from "../../../../../src/production-traces/contract/types.js";
import {
  incomingDir,
} from "../../../../../src/production-traces/ingest/paths.js";

/**
 * Build a syntactically valid ProductionTrace. Any fields can be overridden;
 * sensible defaults fill the rest. The `traceId` is a freshly-minted ULID
 * unless one is supplied.
 */
export function makeTrace(overrides: {
  readonly traceId?: ProductionTraceId;
  readonly startedAt?: string;
  readonly endedAt?: string;
  readonly env?: Partial<ProductionTrace["env"]>;
  readonly outcome?: ProductionTrace["outcome"];
  readonly messages?: ProductionTrace["messages"];
  readonly links?: ProductionTrace["links"];
} = {}): ProductionTrace {
  const traceId = overrides.traceId ?? newProductionTraceId();
  const startedAt = overrides.startedAt ?? "2026-04-17T12:00:00.000Z";
  const endedAt =
    overrides.endedAt ?? new Date(Date.parse(startedAt) + 1000).toISOString();
  return {
    schemaVersion: "1.0",
    traceId,
    source: {
      emitter: "sdk",
      sdk: { name: "autoctx-ts", version: "0.4.3" },
    },
    provider: { name: "openai" },
    model: "gpt-4o-mini",
    env: {
      environmentTag: "production" as ProductionTrace["env"]["environmentTag"],
      appId: "my-app" as ProductionTrace["env"]["appId"],
      ...overrides.env,
    },
    messages: overrides.messages ?? [
      { role: "user", content: "hello", timestamp: startedAt },
    ],
    toolCalls: [],
    timing: { startedAt, endedAt, latencyMs: 1000 },
    usage: { tokensIn: 10, tokensOut: 5 },
    feedbackRefs: [],
    ...(overrides.links ? { links: overrides.links } : { links: {} }),
    redactions: [],
    ...(overrides.outcome !== undefined ? { outcome: overrides.outcome } : {}),
  };
}

/**
 * Write a batch of traces to `.autocontext/production-traces/incoming/<date>/<batchId>.jsonl`.
 * Returns the absolute path for follow-up assertions.
 */
export function writeIncomingBatch(
  cwd: string,
  date: string,
  batchId: string,
  traces: readonly ProductionTrace[],
): string {
  const dir = incomingDir(cwd, date);
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${batchId}.jsonl`);
  const body = traces.map((t) => JSON.stringify(t)).join("\n") + (traces.length ? "\n" : "");
  writeFileSync(path, body, "utf-8");
  return path;
}

/** ISO date + time fixture (fixed timestamps for deterministic assertions). */
export const TEST_DATE = "2026-04-17";
export const TEST_NOW = "2026-04-17T13:00:00.000Z";
