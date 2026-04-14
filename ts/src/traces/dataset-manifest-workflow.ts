import { existsSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import type {
  DiscoveredDataset,
  DiscoveryManifest,
} from "./dataset-discovery-types.js";
import {
  detectDatasetFormat,
  resolveRepoLocalDatasetPath,
} from "./dataset-path-resolution-workflow.js";

export function collectManifestDatasets(repoRoot: string): DiscoveredDataset[] {
  const resolvedRoot = resolve(repoRoot);
  const manifestPath = join(resolvedRoot, ".autoctx-data.json");
  if (!existsSync(manifestPath)) {
    return [];
  }

  try {
    const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as DiscoveryManifest;
    return (manifest.datasets ?? []).flatMap((entry) => {
      const absolutePath = resolveRepoLocalDatasetPath(resolvedRoot, entry.path);
      if (!absolutePath || !existsSync(absolutePath)) {
        return [];
      }

      return [{
        absolutePath,
        relativePath: relative(resolvedRoot, absolutePath),
        format: detectDatasetFormat(entry.path, entry.format),
        source: "manifest" as const,
        scenario: entry.scenario,
      }];
    });
  } catch {
    return [];
  }
}
