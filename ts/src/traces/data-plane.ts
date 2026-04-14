/**
 * Trace-to-disposable-model data plane (AC-466).
 *
 * Basic dataset curation with score filtering, held-out splits, and consent.
 *
 * NOTE: For production use, prefer DistillationPipeline (AC-458) which
 * extends this with gate filtering, top-quartile selection, family
 * filtering, failure-example policy, and richer manifests.
 *
 * Orchestrates the pipeline from raw traces → curated dataset → training inputs.
 *
 * DatasetCurator: filters, scores, splits held-out, enforces consent.
 * DataPlane: ingest → curate → output ShareGPT JSONL + manifest.
 *
 * This is the program-level orchestrator that ties AC-462 (schema),
 * AC-464 (redaction), AC-463 (export), AC-465 (publishing) together
 * into a single dataset construction pipeline.
 */

import {
  curateTraceEntries,
  normalizeCurationPolicy,
  type NormalizedCurationPolicy,
} from "./data-plane-curation-workflow.js";
import {
  buildCompletedDataPlaneResult,
  buildDataPlaneStatus,
  buildFailedDataPlaneResult,
  loadTraceEntries,
  writeCuratedDatasetArtifacts,
} from "./data-plane-io-workflow.js";
import type {
  CuratedDataset,
  CurationPolicy,
  DataPlaneBuildResult,
  DataPlaneConfig,
  DataPlaneStatus,
  TraceEntry,
} from "./data-plane-types.js";

export type {
  CuratedDataset,
  CurationPolicy,
  DataPlaneBuildResult,
  DataPlaneConfig,
  DataPlaneStatus,
  TraceEntry,
} from "./data-plane-types.js";

export class DatasetCurator {
  private policy: NormalizedCurationPolicy;

  constructor(policy?: CurationPolicy) {
    this.policy = normalizeCurationPolicy(policy);
  }

  curate(traceDir: string): CuratedDataset {
    const entries = loadTraceEntries(traceDir);
    return curateTraceEntries(entries, this.policy);
  }
}

export class DataPlane {
  private config: DataPlaneConfig;
  private lastResult?: DataPlaneBuildResult;

  constructor(config: DataPlaneConfig) {
    this.config = config;
  }

  async build(): Promise<DataPlaneBuildResult> {
    try {
      const curator = new DatasetCurator(this.config.curationPolicy);
      const dataset = curator.curate(this.config.traceDir);
      const { manifest } = writeCuratedDatasetArtifacts({
        outputDir: this.config.outputDir,
        dataset,
        curationPolicy: this.config.curationPolicy,
      });
      const result = buildCompletedDataPlaneResult(this.config.outputDir, manifest);
      this.lastResult = result;
      return result;
    } catch (error) {
      const result = buildFailedDataPlaneResult(this.config.outputDir, error);
      this.lastResult = result;
      return result;
    }
  }

  status(): DataPlaneStatus {
    return buildDataPlaneStatus(this.config.outputDir, this.lastResult);
  }
}
