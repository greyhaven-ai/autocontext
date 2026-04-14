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
  DistillationLoadResult,
  DistillationManifest,
  DistillationPolicy,
  TraceEntry,
} from "./distillation-types.js";

export function toShareGPT(
  trace: TraceEntry["trace"],
  extraMetadata?: Record<string, unknown>,
): Record<string, unknown> {
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
      score: (trace.outcome as Record<string, unknown> | undefined)?.score,
      ...extraMetadata,
    },
  };
}

export function ensureDistillationOutputDir(outputDir: string): void {
  if (!existsSync(outputDir)) {
    mkdirSync(outputDir, { recursive: true });
  }
}

export function loadDistillationEntries(traceDir: string): DistillationLoadResult {
  if (!existsSync(traceDir)) {
    return { entries: [], warnings: [] };
  }

  const entries: TraceEntry[] = [];
  const warnings: string[] = [];
  let files: string[];
  try {
    files = readdirSync(traceDir).filter((file: string) => file.endsWith(".json")).sort();
  } catch (err) {
    warnings.push(
      `Could not read trace directory '${traceDir}': ${err instanceof Error ? err.message : String(err)}`,
    );
    return { entries, warnings };
  }

  for (const file of files) {
    try {
      const raw = readFileSync(join(traceDir, file), "utf-8");
      const parsed = JSON.parse(raw) as TraceEntry;
      if (parsed.trace?.traceId && parsed.manifest && parsed.attestation) {
        entries.push(parsed);
      } else {
        warnings.push(`${file}: missing trace, manifest, or attestation`);
      }
    } catch (err) {
      warnings.push(`${file}: ${err instanceof Error ? err.message : "parse error"}`);
    }
  }

  return { entries, warnings };
}

export function writeDistillationJsonl(
  path: string,
  entries: TraceEntry[],
  extraMetadata?: Record<string, unknown>,
): void {
  writeFileSync(path, "", "utf-8");
  for (const entry of entries) {
    appendFileSync(path, `${JSON.stringify(toShareGPT(entry.trace, extraMetadata))}\n`, "utf-8");
  }
}

export function buildDistillationManifest(opts: {
  totalTraces: number;
  includedTraces: number;
  excludedTraces: number;
  trainSize: number;
  heldOutSize: number;
  evalOnlySize: number;
  contrastiveSize: number;
  curationPolicy: DistillationPolicy;
  sources: Record<string, number>;
}): DistillationManifest {
  return {
    totalTraces: opts.totalTraces,
    includedTraces: opts.includedTraces,
    excludedTraces: opts.excludedTraces,
    trainSize: opts.trainSize,
    heldOutSize: opts.heldOutSize,
    evalOnlySize: opts.evalOnlySize,
    contrastiveSize: opts.contrastiveSize,
    curationPolicy: opts.curationPolicy,
    sources: opts.sources,
    createdAt: new Date().toISOString(),
  };
}

export function writeDistillationManifest(
  outputDir: string,
  manifest: DistillationManifest,
): void {
  writeFileSync(
    join(outputDir, "manifest.json"),
    JSON.stringify(manifest, null, 2),
    "utf-8",
  );
}
