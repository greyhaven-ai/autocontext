import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";

import type { IngestResult, TraceArtifact } from "./publishers-types.js";

export function loadSeenTraceIds(cacheDir: string): Set<string> {
  const seenIds = new Set<string>();
  if (!existsSync(cacheDir)) {
    return seenIds;
  }

  try {
    const files = readdirSync(cacheDir) as string[];
    for (const file of files) {
      if (file.endsWith(".json")) {
        seenIds.add(file.replace(".json", ""));
      }
    }
  } catch {
    // empty cache or unreadable cache directory
  }

  return seenIds;
}

export async function ingestPublishedTraceFile(opts: {
  filePath: string;
  cacheDir: string;
  seenIds: Set<string>;
}): Promise<IngestResult> {
  if (!existsSync(opts.filePath)) {
    return {
      status: "failed",
      tracesIngested: 0,
      duplicatesSkipped: 0,
      error: `File not found: ${opts.filePath}`,
    };
  }

  if (!existsSync(opts.cacheDir)) {
    mkdirSync(opts.cacheDir, { recursive: true });
  }

  const content = readFileSync(opts.filePath, "utf-8");
  const lines = content.trim().split("\n").filter(Boolean);
  let ingested = 0;
  let duplicates = 0;

  for (const line of lines) {
    try {
      const artifact = JSON.parse(line) as TraceArtifact;
      const traceId = artifact.trace?.traceId;
      if (!traceId) {
        continue;
      }
      if (opts.seenIds.has(traceId)) {
        duplicates += 1;
        continue;
      }

      writeFileSync(
        join(opts.cacheDir, `${traceId}.json`),
        JSON.stringify(artifact, null, 2),
        "utf-8",
      );
      opts.seenIds.add(traceId);
      ingested += 1;
    } catch {
      // Skip malformed lines
    }
  }

  return {
    status: "ingested",
    tracesIngested: ingested,
    duplicatesSkipped: duplicates,
    cacheDir: opts.cacheDir,
  };
}
