import type { AdaptedDataset } from "./dataset-discovery-types.js";
import type {
  DatasetProvenance,
  DiscoveredDataset,
  ShareGPTRecord,
} from "./dataset-discovery-types.js";
import { buildAdaptedDatasetResult } from "./dataset-adapter-routing-workflow.js";
import { buildDatasetProvenance } from "./dataset-adapter-provenance.js";

export { buildDatasetProvenance } from "./dataset-adapter-provenance.js";
export {
  adaptDatasetRecords,
  buildAdaptedDatasetResult,
  buildDatasetNotFoundResult,
} from "./dataset-adapter-routing-workflow.js";
export {
  adaptJsonDataset,
  adaptJsonlDataset,
  ioPairToShareGPT,
} from "./dataset-json-adapter-workflow.js";
export {
  adaptCsvDataset,
  parseCSVLine,
} from "./dataset-csv-adapter-workflow.js";
export {
  adaptMarkdownDataset,
  findMarkdownSection,
  normalizeMarkdownHeading,
  parseMarkdownSections,
} from "./dataset-markdown-adapter-workflow.js";

export function adaptDiscoveredDataset(dataset: DiscoveredDataset): AdaptedDataset {
  return buildAdaptedDatasetResult({
    dataset,
    provenance: buildDatasetProvenance(dataset),
  });
}

export type {
  DatasetProvenance,
  DiscoveredDataset,
  ShareGPTRecord,
};
