import { createProductionTrace } from "../../../../../src/production-traces/contract/factories.js";
import type { ProductionTrace } from "../../../../../src/production-traces/contract/types.js";
import type {
  AppId,
  EnvironmentTag,
  ProductionTraceId,
  Scenario,
} from "../../../../../src/production-traces/contract/branded-ids.js";
import {
  parseProductionTraceId,
  parseScenario,
} from "../../../../../src/production-traces/contract/branded-ids.js";
import type { LoadedRedactionPolicy } from "../../../../../src/production-traces/redaction/types.js";

/**
 * Minimal inputs for a ProductionTrace factory call — saves a lot of
 * boilerplate in dataset tests.
 */
function baseFactoryInputs(overrides: {
  readonly traceId?: string;
  readonly taskType?: string;
  readonly startedAt?: string;
  readonly scenarioId?: string;
  readonly outcome?: ProductionTrace["outcome"];
  readonly messages?: ProductionTrace["messages"];
  readonly toolCalls?: ProductionTrace["toolCalls"];
} = {}) {
  const startedAt = overrides.startedAt ?? "2026-04-17T12:00:00.000Z";
  const endedAt = new Date(Date.parse(startedAt) + 1000).toISOString();
  const id = overrides.traceId !== undefined
    ? parseProductionTraceId(overrides.traceId)
    : null;
  const scenarioId = overrides.scenarioId !== undefined
    ? parseScenario(overrides.scenarioId)
    : undefined;
  if (overrides.traceId !== undefined && id === null) {
    throw new Error(`fixture traceId ${overrides.traceId} is not a valid ULID`);
  }
  if (overrides.scenarioId !== undefined && scenarioId === null) {
    throw new Error(`fixture scenarioId ${overrides.scenarioId} is not valid`);
  }
  return {
    id: id ?? undefined,
    source: { emitter: "sdk", sdk: { name: "autoctx-ts", version: "0.4.3" } },
    provider: { name: "openai" as const },
    model: "gpt-4o-mini",
    env: {
      environmentTag: "production" as EnvironmentTag,
      appId: "my-app" as AppId,
      ...(overrides.taskType !== undefined ? { taskType: overrides.taskType } : {}),
    },
    messages: overrides.messages ?? [
      { role: "user" as const, content: "hi", timestamp: startedAt },
    ],
    toolCalls: overrides.toolCalls ?? [],
    outcome: overrides.outcome,
    timing: { startedAt, endedAt, latencyMs: 1000 },
    usage: { tokensIn: 10, tokensOut: 5 },
    links: scenarioId ? { scenarioId: scenarioId as Scenario } : {},
  };
}

export function makeTrace(overrides: Parameters<typeof baseFactoryInputs>[0] = {}): ProductionTrace {
  return createProductionTrace(baseFactoryInputs(overrides));
}

/**
 * Construct a short ordered list of traces with deterministic ULIDs —
 * lexicographically ordered so sorted output == insertion order.
 */
export function makeTraceBatch(count: number, extras: {
  readonly taskType?: string;
  readonly startedAt?: string;
} = {}): ProductionTrace[] {
  const base = extras.startedAt ?? "2026-04-17T12:00:00.000Z";
  const result: ProductionTrace[] = [];
  for (let i = 0; i < count; i += 1) {
    // 26-char Crockford ULIDs: we hand-build deterministic IDs with a monotone suffix.
    const suffix = i.toString().padStart(4, "0");
    const ulid = `01K000000000000000000A${suffix}`.slice(0, 26);
    const startedAt = new Date(Date.parse(base) + i * 1000).toISOString();
    result.push(makeTrace({ traceId: ulid, startedAt, taskType: extras.taskType }));
  }
  return result;
}

/**
 * Minimal policy mirroring `defaultRedactionPolicy()` but inlined here so
 * dataset tests don't need to import the whole redaction module.
 */
export const MINIMAL_POLICY: LoadedRedactionPolicy = {
  schemaVersion: "1.0",
  mode: "on-export",
  autoDetect: { enabled: false, categories: [] },
  customPatterns: [],
  rawProviderPayload: { behavior: "blanket-mark" },
  exportPolicy: {
    placeholder: "[redacted]",
    preserveLength: false,
    includeRawProviderPayload: false,
    includeMetadata: true,
    categoryOverrides: {},
  },
};

/** Stable ULIDs for trace IDs in tests. Avoids `as` casts at call sites. */
export function traceIdOf(s: string): ProductionTraceId {
  const parsed = parseProductionTraceId(s);
  if (parsed === null) {
    throw new Error(`fixture: ${s} is not a valid ProductionTraceId`);
  }
  return parsed;
}
