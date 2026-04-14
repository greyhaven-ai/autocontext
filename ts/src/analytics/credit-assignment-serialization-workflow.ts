import { roundToDecimals } from "./number-utils.js";
import type {
  AttributionResultDict,
  ComponentChangeDict,
  CreditAssignmentRecordDict,
  GenerationChangeVectorDict,
} from "./credit-assignment-contracts.js";

export function normalizeComponentChangeData(
  data: Record<string, unknown> | ComponentChangeDict,
): {
  component: string;
  magnitude: number;
  description: string;
  metadata: Record<string, unknown>;
} {
  return {
    component: String(data.component ?? ""),
    magnitude: Number(data.magnitude ?? 0),
    description: String(data.description ?? ""),
    metadata: (data.metadata as Record<string, unknown>) ?? {},
  };
}

export function buildComponentChangeDict(change: {
  component: string;
  magnitude: number;
  description: string;
  metadata: Record<string, unknown>;
}): ComponentChangeDict {
  return {
    component: change.component,
    magnitude: change.magnitude,
    description: change.description,
    metadata: change.metadata,
  };
}

export function buildGenerationChangeVectorDict(vector: {
  generation: number;
  scoreDelta: number;
  changes: Array<{ toDict(): ComponentChangeDict }>;
  metadata: Record<string, unknown>;
}): GenerationChangeVectorDict {
  return {
    generation: vector.generation,
    score_delta: vector.scoreDelta,
    changes: vector.changes.map((change) => change.toDict()),
    metadata: vector.metadata,
  };
}

export function normalizeGenerationChangeVectorData(
  data: Record<string, unknown> | GenerationChangeVectorDict,
): {
  generation: number;
  scoreDelta: number;
  changes: Record<string, unknown>[];
  metadata: Record<string, unknown>;
} {
  return {
    generation: Number(data.generation ?? 0),
    scoreDelta: Number(data.score_delta ?? 0),
    changes: Array.isArray(data.changes)
      ? data.changes.filter((change): change is Record<string, unknown> => Boolean(change) && typeof change === "object")
      : [],
    metadata: (data.metadata as Record<string, unknown>) ?? {},
  };
}

export function normalizeCreditsMap(
  rawCredits: unknown,
): Record<string, number> {
  const credits: Record<string, number> = {};
  if (rawCredits && typeof rawCredits === "object" && !Array.isArray(rawCredits)) {
    for (const [component, value] of Object.entries(rawCredits)) {
      credits[String(component)] = Number(value);
    }
  }
  return credits;
}

export function buildAttributionResultDict(result: {
  generation: number;
  totalDelta: number;
  credits: Record<string, number>;
  metadata: Record<string, unknown>;
}): AttributionResultDict {
  return {
    generation: result.generation,
    total_delta: result.totalDelta,
    credits: result.credits,
    metadata: result.metadata,
  };
}

export function normalizeAttributionResultData(
  data: Record<string, unknown> | AttributionResultDict,
): {
  generation: number;
  totalDelta: number;
  credits: Record<string, number>;
  metadata: Record<string, unknown>;
} {
  return {
    generation: Number(data.generation ?? 0),
    totalDelta: Number(data.total_delta ?? 0),
    credits: normalizeCreditsMap(data.credits),
    metadata: (data.metadata as Record<string, unknown>) ?? {},
  };
}

export function buildCreditAssignmentRecordDict(record: {
  runId: string;
  generation: number;
  vector: { toDict(): GenerationChangeVectorDict };
  attribution: { toDict(): AttributionResultDict };
  metadata: Record<string, unknown>;
}): CreditAssignmentRecordDict {
  return {
    run_id: record.runId,
    generation: record.generation,
    vector: record.vector.toDict(),
    attribution: record.attribution.toDict(),
    metadata: record.metadata,
  };
}

export function normalizeCreditAssignmentRecordData(
  data: Record<string, unknown> | CreditAssignmentRecordDict,
): {
  runId: string;
  generation: number;
  vector: Record<string, unknown>;
  attribution: Record<string, unknown>;
  metadata: Record<string, unknown>;
} {
  return {
    runId: String(data.run_id ?? ""),
    generation: Number(data.generation ?? 0),
    vector: (data.vector as Record<string, unknown>) ?? {},
    attribution: (data.attribution as Record<string, unknown>) ?? {},
    metadata: (data.metadata as Record<string, unknown>) ?? {},
  };
}

export function computeTotalChangeMagnitude(
  changes: Array<{ magnitude: number }>,
): number {
  return roundToDecimals(changes.reduce((sum, change) => sum + change.magnitude, 0), 6);
}

export function buildZeroCredits(
  changes: Array<{ component: string }>,
): Record<string, number> {
  return Object.fromEntries(changes.map((change) => [change.component, 0]));
}
