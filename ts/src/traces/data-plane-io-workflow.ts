import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";

import type {
  CuratedDataset,
  CurationPolicy,
  DataPlaneBuildResult,
  DataPlaneStatus,
  TraceEntry,
} from "./data-plane-types.js";
import type { PublicTrace } from "./public-schema.js";

export function loadTraceEntries(traceDir: string): TraceEntry[] {
  if (!existsSync(traceDir)) {
    return [];
  }

  let files: string[];
  try {
    files = readdirSync(traceDir).filter((file: string) => file.endsWith(".json")).sort();
  } catch {
    return [];
  }

  const entries: TraceEntry[] = [];
  for (const file of files) {
    try {
      const raw = readFileSync(join(traceDir, file), "utf-8");
      const parsed = JSON.parse(raw) as TraceEntry;
      if (parsed.trace?.traceId && parsed.manifest && parsed.attestation) {
        entries.push(parsed);
      }
    } catch {
      // Skip malformed files
    }
  }

  return entries;
}

export function toShareGptTraceRow(trace: PublicTrace): Record<string, unknown> {
  const roleMap: Record<string, string> = {
    user: "human",
    assistant: "gpt",
    system: "system",
    tool: "tool",
  };

  return {
    conversations: trace.messages.map((message) => ({
      from: roleMap[message.role] ?? message.role,
      value: message.content,
    })),
    metadata: {
      traceId: trace.traceId,
      sourceHarness: trace.sourceHarness,
      score: (trace.outcome as { score?: number } | undefined)?.score,
    },
  };
}

export function summarizeDataPlaneSources(entries: TraceEntry[]): Record<string, number> {
  const sources = new Map<string, number>();
  for (const entry of entries) {
    const source = entry.manifest.sourceHarness;
    sources.set(source, (sources.get(source) ?? 0) + 1);
  }
  return Object.fromEntries(sources);
}

export function writeCuratedDatasetArtifacts(opts: {
  outputDir: string;
  dataset: CuratedDataset;
  curationPolicy?: CurationPolicy;
}): {
  manifest: {
    totalTraces: number;
    includedTraces: number;
    excludedTraces: number;
    trainSize: number;
    heldOutSize: number;
    curationPolicy: CurationPolicy;
    sources: Record<string, number>;
    createdAt: string;
  };
} {
  if (!existsSync(opts.outputDir)) {
    mkdirSync(opts.outputDir, { recursive: true });
  }

  const trainPath = join(opts.outputDir, "train.jsonl");
  writeFileSync(trainPath, "", "utf-8");
  for (const entry of opts.dataset.train) {
    appendFileSync(trainPath, `${JSON.stringify(toShareGptTraceRow(entry.trace))}\n`, "utf-8");
  }

  if (opts.dataset.heldOut.length > 0) {
    const heldOutPath = join(opts.outputDir, "held_out.jsonl");
    writeFileSync(heldOutPath, "", "utf-8");
    for (const entry of opts.dataset.heldOut) {
      appendFileSync(heldOutPath, `${JSON.stringify(toShareGptTraceRow(entry.trace))}\n`, "utf-8");
    }
  }

  const manifest = {
    totalTraces: opts.dataset.included.length + opts.dataset.excluded.length,
    includedTraces: opts.dataset.included.length,
    excludedTraces: opts.dataset.excluded.length,
    trainSize: opts.dataset.train.length,
    heldOutSize: opts.dataset.heldOut.length,
    curationPolicy: opts.curationPolicy ?? {},
    sources: summarizeDataPlaneSources(opts.dataset.included),
    createdAt: new Date().toISOString(),
  };

  writeFileSync(
    join(opts.outputDir, "manifest.json"),
    JSON.stringify(manifest, null, 2),
    "utf-8",
  );

  return { manifest };
}

export function buildCompletedDataPlaneResult(
  outputDir: string,
  manifest: {
    totalTraces: number;
    includedTraces: number;
    excludedTraces: number;
    trainSize: number;
    heldOutSize: number;
  },
): DataPlaneBuildResult {
  return {
    status: "completed",
    totalTraces: manifest.totalTraces,
    includedTraces: manifest.includedTraces,
    excludedTraces: manifest.excludedTraces,
    trainSize: manifest.trainSize,
    heldOutSize: manifest.heldOutSize,
    outputDir,
  };
}

export function buildFailedDataPlaneResult(
  outputDir: string,
  error: unknown,
): DataPlaneBuildResult {
  return {
    status: "failed",
    totalTraces: 0,
    includedTraces: 0,
    excludedTraces: 0,
    trainSize: 0,
    heldOutSize: 0,
    outputDir,
    error: error instanceof Error ? error.message : String(error),
  };
}

export function buildDataPlaneStatus(
  outputDir: string,
  lastResult?: DataPlaneBuildResult,
): DataPlaneStatus {
  return {
    totalTraces: lastResult?.totalTraces ?? 0,
    includedTraces: lastResult?.includedTraces ?? 0,
    trainSize: lastResult?.trainSize ?? 0,
    heldOutSize: lastResult?.heldOutSize ?? 0,
    outputDir,
    built: lastResult?.status === "completed",
  };
}
