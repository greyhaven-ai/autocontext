import type {
  DatasetProvenance,
  DiscoveredDataset,
} from "./dataset-discovery-types.js";

export function buildDatasetProvenance(dataset: DiscoveredDataset): DatasetProvenance {
  return {
    sourcePath: dataset.relativePath,
    sourceFormat: dataset.format,
    scenario: dataset.scenario,
    adaptedAt: new Date().toISOString(),
    transformationMethod: `adapt_${dataset.format}`,
  };
}
