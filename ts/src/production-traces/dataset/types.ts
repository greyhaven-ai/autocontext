/**
 * Hand-written TypeScript shapes for the dataset-generation pipeline.
 *
 * These mirror the generated types in `../contract/generated-types.ts` but with
 * tighter-branding (`DatasetId` nominal type, `ContentHash` for provenance
 * hashes, `ProductionTraceId` references) and richer JSDoc.
 *
 * As with `redaction/types.ts`, hand-writing lets us keep `as const` friendliness
 * and narrow literal unions at the TS level while the JSON Schema remains the
 * on-disk contract.
 */
import type {
  ContentHash,
  ProductionTraceId,
  Scenario,
} from "../contract/branded-ids.js";
import type { TraceMessage } from "../contract/types.js";
import type { LoadedRedactionPolicy } from "../redaction/types.js";
import type { ProductionTrace } from "../contract/types.js";

// ---- Branded DatasetId ------------------------------------------------------

declare const datasetIdBrand: unique symbol;
export type DatasetId = string & { readonly [datasetIdBrand]: "DatasetId" };

const DATASET_ID_RE = /^ds_[0-9A-HJKMNP-TV-Z]{26}$/;

export function parseDatasetId(input: string): DatasetId | null {
  return DATASET_ID_RE.test(input) ? (input as DatasetId) : null;
}

// ---- DatasetRow -------------------------------------------------------------

export type DatasetRowSplit = "train" | "eval" | "holdout";

export type Rubric = {
  readonly rubricId: string;
  readonly dimensions: readonly string[];
  readonly description?: string;
};

export type RubricSource = "explicit" | "registry" | "synthetic";

export type ExpectedOutcome = {
  readonly label: "success" | "failure" | "partial";
  readonly score?: number;
  readonly reasoning?: string;
};

export type DatasetRow = {
  readonly schemaVersion: "1.0";
  readonly rowId: string;
  readonly split: DatasetRowSplit;
  readonly clusterId: string;
  readonly source: {
    readonly traceIds: readonly ProductionTraceId[];
    readonly timeRange: { readonly from: string; readonly to: string };
    readonly redactionApplied: boolean;
  };
  readonly inputs: {
    readonly messages: readonly TraceMessage[];
    readonly toolsAvailable: readonly string[];
  };
  readonly expectedOutcome?: ExpectedOutcome;
  readonly rubric?: {
    readonly rubricId: string;
    readonly dimensions: readonly string[];
    readonly source: RubricSource;
  };
  readonly metadata: Readonly<Record<string, unknown>>;
};

// ---- DatasetManifest --------------------------------------------------------

export type ClusterStrategy = "taskType" | "rules";

export type SplitStats = {
  readonly rowCount: number;
  readonly fileHash: ContentHash;
};

export type ManifestClusterEntry = {
  readonly clusterId: string;
  readonly size: number;
  readonly rubricId?: string;
  readonly rubricSource?: RubricSource;
  readonly skippedReason?: string;
};

export type DatasetManifest = {
  readonly schemaVersion: "1.0";
  readonly datasetId: DatasetId;
  readonly name: string;
  readonly description: string;
  readonly createdAt: string;
  readonly autoctxVersion: string;
  readonly source: {
    readonly traceCount: number;
    readonly timeRange: { readonly from: string; readonly to: string };
    readonly clusterStrategy: ClusterStrategy;
    readonly filterRules: readonly SelectionRule[];
    readonly redactionPolicy: {
      readonly mode: "on-export" | "on-ingest";
      readonly snapshotHash: ContentHash;
    };
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
};

// ---- Selection rules --------------------------------------------------------

export type MatchOperator = {
  readonly equals?: unknown;
  readonly contains?: string | readonly string[];
  readonly default?: true;
};

export type MatchExpression = Readonly<Record<string, MatchOperator>>;

export type GateRule = {
  readonly type: "gate";
  readonly include?: readonly MatchExpression[];
  readonly exclude?: readonly MatchExpression[];
};

export type TopQuartileRule = {
  readonly type: "top-quartile";
  readonly by: string;
  readonly percentile: number;
  readonly perCluster?: boolean;
};

export type ContrastiveRule = {
  readonly type: "contrastive";
  readonly failureCriterion: MatchExpression;
  readonly successCriterion: MatchExpression;
  readonly pairStrategy?: "same-cluster";
  readonly maxPairsPerCluster?: number;
};

export type SplitRule = {
  readonly type: "split";
  readonly train: number;
  readonly eval: number;
  readonly holdout: number;
  readonly shuffle?: boolean;
  readonly seed?: number;
};

export type SelectionRule =
  | GateRule
  | TopQuartileRule
  | ContrastiveRule
  | SplitRule;

// ---- Cluster + rubric configs ----------------------------------------------

export type ClusterConfig = {
  readonly strategy: "rules";
  readonly rules: readonly {
    readonly id: string;
    readonly match: MatchExpression;
  }[];
};

export type RubricConfigEntry =
  | { readonly source: "file"; readonly path: string }
  | { readonly source: "inline"; readonly rubric: Rubric };

export type RubricConfig = {
  readonly rubricsByCluster: Readonly<Record<string, RubricConfigEntry>>;
};

// ---- Rubric resolution ------------------------------------------------------

export type RubricLookup = (scenarioId: Scenario) => Promise<Rubric | null>;

export type RubricResolution =
  | { readonly source: "explicit"; readonly rubric: Rubric }
  | { readonly source: "registry"; readonly rubric: Rubric }
  | { readonly source: "synthetic"; readonly rubric: Rubric }
  | { readonly source: "skip"; readonly skipReason: string };

// ---- Pipeline I/O -----------------------------------------------------------

export interface BuildDatasetInputs {
  readonly cwd: string;
  readonly name: string;
  readonly description?: string;
  readonly traces: readonly ProductionTrace[];
  readonly clusterStrategy: ClusterStrategy;
  readonly clusterConfig?: ClusterConfig;
  readonly selectionRules: readonly SelectionRule[];
  readonly rubricConfig?: RubricConfig;
  readonly rubricLookup?: RubricLookup;
  readonly allowSyntheticRubrics: boolean;
  readonly redactionPolicy: LoadedRedactionPolicy;
  readonly installSalt: string | null;
  readonly seed: number;
  /** If true, override content-addressed ID with a fresh time-ordered ULID. */
  readonly newId?: boolean;
  readonly autoctxVersion: string;
}

export interface BuildDatasetStats {
  readonly traceCount: number;
  readonly clusterCount: number;
  readonly clustersSkipped: number;
  readonly splitSizes: { readonly train: number; readonly eval: number; readonly holdout: number };
}

export interface BuildDatasetResult {
  readonly datasetId: DatasetId;
  readonly manifest: DatasetManifest;
  readonly writePath: string;
  readonly stats: BuildDatasetStats;
}
