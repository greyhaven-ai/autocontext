import { existsSync } from "node:fs";

import type {
  AdaptedDataset,
  DatasetProvenance,
  DiscoveredDataset,
  ShareGPTRecord,
} from "./dataset-discovery-types.js";
import { adaptCsvDataset } from "./dataset-csv-adapter-workflow.js";
import { adaptJsonDataset, adaptJsonlDataset } from "./dataset-json-adapter-workflow.js";
import { adaptMarkdownDataset } from "./dataset-markdown-adapter-workflow.js";

export function adaptDatasetRecords(
  dataset: DiscoveredDataset,
  warnings: string[],
): ShareGPTRecord[] {
  switch (dataset.format) {
    case "jsonl":
      return adaptJsonlDataset(dataset.absolutePath, warnings);
    case "json":
      return adaptJsonDataset(dataset.absolutePath);
    case "csv":
      return adaptCsvDataset(dataset.absolutePath);
    case "markdown":
      return adaptMarkdownDataset(dataset.absolutePath);
    default:
      throw new Error(`Unsupported format: ${dataset.format}`);
  }
}

export function buildDatasetNotFoundResult(
  dataset: DiscoveredDataset,
  provenance: DatasetProvenance,
): AdaptedDataset {
  return {
    status: "failed",
    records: [],
    provenance,
    warnings: [],
    error: `File not found: ${dataset.absolutePath}`,
  };
}

export function buildAdaptedDatasetResult(opts: {
  dataset: DiscoveredDataset;
  provenance: DatasetProvenance;
}): AdaptedDataset {
  if (!existsSync(opts.dataset.absolutePath)) {
    return buildDatasetNotFoundResult(opts.dataset, opts.provenance);
  }

  try {
    const warnings: string[] = [];
    const records = adaptDatasetRecords(opts.dataset, warnings);
    return {
      status: "adapted",
      records,
      provenance: opts.provenance,
      warnings,
    };
  } catch (err) {
    return {
      status: "failed",
      records: [],
      provenance: opts.provenance,
      warnings: [],
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
