/**
 * Pure dataset-manifest assembly (spec §8.4 DatasetManifest shape).
 *
 * Assembles the manifest structure from pre-computed stats. Does NOT
 * perform any I/O — the orchestrator is responsible for writing the result
 * to `.autocontext/datasets/<datasetId>/manifest.json`.
 */
import type {
  ClusterStrategy,
  DatasetId,
  DatasetManifest,
  ManifestClusterEntry,
  SelectionRule,
  SplitStats,
} from "./types.js";
import type { ContentHash } from "../contract/branded-ids.js";

export interface BuildManifestInputs {
  readonly datasetId: DatasetId;
  readonly name: string;
  readonly description: string;
  readonly createdAt: string;
  readonly autoctxVersion: string;
  readonly traceCount: number;
  readonly timeRange: { readonly from: string; readonly to: string };
  readonly clusterStrategy: ClusterStrategy;
  readonly filterRules: readonly SelectionRule[];
  readonly redactionPolicy: {
    readonly mode: "on-export" | "on-ingest";
    readonly snapshotHash: ContentHash;
  };
  readonly splits: {
    readonly train: SplitStats;
    readonly eval: SplitStats;
    readonly holdout: SplitStats;
  };
  readonly clusters: readonly ManifestClusterEntry[];
  readonly provenance: {
    readonly configHash: ContentHash;
    readonly inputTracesHash: ContentHash;
  };
}

export function buildManifest(inputs: BuildManifestInputs): DatasetManifest {
  return {
    schemaVersion: "1.0",
    datasetId: inputs.datasetId,
    name: inputs.name,
    description: inputs.description,
    createdAt: inputs.createdAt,
    autoctxVersion: inputs.autoctxVersion,
    source: {
      traceCount: inputs.traceCount,
      timeRange: inputs.timeRange,
      clusterStrategy: inputs.clusterStrategy,
      filterRules: inputs.filterRules,
      redactionPolicy: inputs.redactionPolicy,
    },
    splits: inputs.splits,
    clusters: inputs.clusters,
    provenance: inputs.provenance,
  };
}
