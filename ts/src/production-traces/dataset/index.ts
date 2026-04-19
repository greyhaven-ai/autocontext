// Public surface of the production-traces dataset-generation module.

export { buildDataset } from "./pipeline.js";
export type {
  BuildDatasetInputs,
  BuildDatasetResult,
  BuildDatasetStats,
  ClusterConfig,
  ClusterStrategy,
  ContrastiveRule,
  DatasetId,
  DatasetManifest,
  DatasetRow,
  DatasetRowSplit,
  ExpectedOutcome,
  GateRule,
  ManifestClusterEntry,
  MatchExpression,
  MatchOperator,
  Rubric,
  RubricConfig,
  RubricConfigEntry,
  RubricLookup,
  RubricResolution,
  RubricSource,
  SelectionRule,
  SplitRule,
  SplitStats,
  TopQuartileRule,
} from "./types.js";
export { parseDatasetId } from "./types.js";

export {
  clusterByRules,
  clusterByTaskType,
  UNCATEGORIZED_CLUSTER,
  matchExpression,
  resolveJsonPath,
} from "./cluster.js";

export {
  applySelectionRules,
  applySelectionRulesPerCluster,
  extractSplitRule,
  rulesWithoutSplit,
} from "./select.js";
export type { SelectionResult, TracePair } from "./select.js";

export { resolveRubric } from "./rubric.js";
export type { ResolveRubricOptions } from "./rubric.js";

export {
  partitionByRatios,
  partitionByRule,
  seededShuffle,
} from "./split.js";
export type { SplitPartitions, SplitRatios } from "./split.js";

export {
  computeConfigHash,
  computeFileHash,
  computeInputTracesHash,
} from "./provenance.js";

export { buildManifest } from "./manifest.js";
export type { BuildManifestInputs } from "./manifest.js";
