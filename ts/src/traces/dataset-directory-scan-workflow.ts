import { existsSync, readdirSync, statSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";

import {
  CONVENTIONAL_DATASET_DIRECTORIES,
  DATASET_FILE_EXTENSIONS,
  IGNORED_DATASET_FILENAMES,
} from "./dataset-discovery-constants.js";
import type { DiscoveredDataset } from "./dataset-discovery-types.js";
import { collectManifestDatasets } from "./dataset-manifest-workflow.js";
import { detectDatasetFormat } from "./dataset-path-resolution-workflow.js";

export function scanConventionalDatasetDirectory(
  dirPath: string,
  repoRoot: string,
  results: DiscoveredDataset[],
  skipPaths: Set<string>,
): void {
  let entries: string[];
  try {
    entries = readdirSync(dirPath);
  } catch {
    return;
  }

  for (const entry of entries) {
    const absolutePath = join(dirPath, entry);
    if (skipPaths.has(absolutePath)) {
      continue;
    }

    try {
      const stat = statSync(absolutePath);
      if (stat.isDirectory()) {
        scanConventionalDatasetDirectory(absolutePath, repoRoot, results, skipPaths);
        continue;
      }
      if (!stat.isFile()) {
        continue;
      }
    } catch {
      continue;
    }

    const extension = extname(entry).toLowerCase();
    if (!DATASET_FILE_EXTENSIONS.has(extension)) {
      continue;
    }
    if (IGNORED_DATASET_FILENAMES.has(entry)) {
      continue;
    }

    results.push({
      absolutePath,
      relativePath: relative(repoRoot, absolutePath),
      format: detectDatasetFormat(absolutePath),
      source: "conventional_dir",
    });
  }
}

export function discoverDatasets(repoRoot: string): DiscoveredDataset[] {
  const resolvedRoot = resolve(repoRoot);
  const manifestDatasets = collectManifestDatasets(resolvedRoot);
  const results = [...manifestDatasets];
  const manifestPaths = new Set(manifestDatasets.map((dataset) => dataset.absolutePath));

  for (const directory of CONVENTIONAL_DATASET_DIRECTORIES) {
    const dirPath = join(resolvedRoot, directory);
    if (!existsSync(dirPath)) {
      continue;
    }
    try {
      if (!statSync(dirPath).isDirectory()) {
        continue;
      }
    } catch {
      continue;
    }

    scanConventionalDatasetDirectory(dirPath, resolvedRoot, results, manifestPaths);
  }

  return results;
}
