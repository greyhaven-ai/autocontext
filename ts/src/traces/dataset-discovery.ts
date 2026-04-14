/**
 * Repo-local dataset discovery and schema adaptation (AC-461).
 *
 * DatasetDiscovery scans a repo tree for candidate training data:
 * - Conventional directories (data/, fixtures/, benchmarks/, examples/)
 * - Manifest files (.autoctx-data.json)
 * - File format detection (JSONL, JSON, CSV)
 *
 * DatasetAdapter converts discovered files into ShareGPT training format
 * with full provenance tracking.
 */

import { adaptDiscoveredDataset } from "./dataset-adapter-workflow.js";
import { discoverDatasets } from "./dataset-discovery-workflow.js";
import type {
  AdaptedDataset,
  DatasetProvenance,
  DatasetFormat,
  DatasetSource,
  DiscoveredDataset,
  DiscoveryManifest,
  ShareGPTRecord,
} from "./dataset-discovery-types.js";

export type {
  AdaptedDataset,
  DatasetProvenance,
  DatasetFormat,
  DatasetSource,
  DiscoveredDataset,
  DiscoveryManifest,
  ShareGPTRecord,
};

export class DatasetDiscovery {
  scan(repoRoot: string): DiscoveredDataset[] {
    return discoverDatasets(repoRoot);
  }
}

export class DatasetAdapter {
  adapt(dataset: DiscoveredDataset): AdaptedDataset {
    return adaptDiscoveredDataset(dataset);
  }
}
