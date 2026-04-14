import { extname, isAbsolute, relative, resolve } from "node:path";

import type { DatasetFormat } from "./dataset-discovery-types.js";

export function resolveRepoLocalDatasetPath(
  repoRoot: string,
  candidatePath: string,
): string | null {
  const absolutePath = resolve(repoRoot, candidatePath);
  const repoRelative = relative(repoRoot, absolutePath);
  if (repoRelative === "" || (!repoRelative.startsWith("..") && !isAbsolute(repoRelative))) {
    return absolutePath;
  }
  return null;
}

export function detectDatasetFormat(path: string, hint?: string): DatasetFormat {
  if (hint) {
    if (hint.includes("jsonl") || hint.includes("sharegpt")) {
      return "jsonl";
    }
    if (hint.includes("json")) {
      return "json";
    }
    if (hint.includes("csv")) {
      return "csv";
    }
    if (hint.includes("markdown") || hint.includes("md")) {
      return "markdown";
    }
  }

  switch (extname(path).toLowerCase()) {
    case ".jsonl":
      return "jsonl";
    case ".json":
      return "json";
    case ".csv":
      return "csv";
    case ".md":
      return "markdown";
    default:
      return "unknown";
  }
}
