export type DatasetFormat = "jsonl" | "json" | "csv" | "markdown" | "unknown";

export type DatasetSource = "manifest" | "conventional_dir" | "file_scan";

export interface DiscoveredDataset {
  absolutePath: string;
  relativePath: string;
  format: DatasetFormat;
  source: DatasetSource;
  scenario?: string;
}

export interface ShareGPTRecord {
  conversations: Array<{ from: string; value: string }>;
  metadata?: Record<string, unknown>;
}

export interface DatasetProvenance {
  sourcePath: string;
  sourceFormat: string;
  scenario?: string;
  adaptedAt: string;
  transformationMethod: string;
}

export interface AdaptedDataset {
  status: "adapted" | "failed";
  records: ShareGPTRecord[];
  provenance: DatasetProvenance;
  warnings: string[];
  error?: string;
}

export interface DiscoveryManifest {
  datasets: Array<{
    path: string;
    format?: string;
    scenario?: string;
  }>;
}
