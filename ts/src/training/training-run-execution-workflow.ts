import {
  type ModelRegistry,
  type PromotionEngine,
} from "./promotion.js";
import {
  ensureCheckpointDir,
  publishTrainingArtifact,
  writeTrainingManifest,
} from "./training-checkpoint-workflow.js";
import { resolveTrainingConfig } from "./training-config-workflow.js";
import { buildFailedTrainingResult } from "./training-result-workflow.js";
import {
  evaluatePromotionState,
  registerPromotionCandidate,
} from "./training-promotion-workflow.js";
import type { BackendRegistry } from "./training-backend-core.js";
import type {
  PublishedArtifact,
  TrainingConfig,
  TrainingExecutor,
  TrainingResult,
} from "./training-types.js";

export async function executeTrainingRunWorkflow(opts: {
  start: number;
  config: TrainingConfig;
  registry: BackendRegistry;
  executor: TrainingExecutor;
  promotionRegistry: ModelRegistry;
  promotionEngine: PromotionEngine;
}): Promise<TrainingResult> {
  try {
    const backend = opts.registry.get(opts.config.backend);
    if (!backend) {
      return buildFailedTrainingResult(
        opts.config.backend,
        opts.start,
        `Unknown training backend: ${opts.config.backend}`,
      );
    }

    if (!backend.isAvailable()) {
      return buildFailedTrainingResult(
        opts.config.backend,
        opts.start,
        `Training backend '${opts.config.backend}' is not available on this machine`,
      );
    }

    const resolution = resolveTrainingConfig(opts.config);
    if (resolution.error) {
      return buildFailedTrainingResult(
        opts.config.backend,
        opts.start,
        resolution.error,
      );
    }

    const checkpointDir = ensureCheckpointDir(
      opts.config.outputDir,
      backend,
      opts.config.scenario,
    );
    writeTrainingManifest(
      checkpointDir,
      resolution.resolvedConfig,
      resolution.datasetSize,
      resolution.heldOutSize,
    );

    const execResult = await opts.executor(
      resolution.resolvedConfig,
      checkpointDir,
    );
    if (!execResult.success) {
      return buildFailedTrainingResult(
        opts.config.backend,
        opts.start,
        execResult.error ?? "Training executor returned failure",
        checkpointDir,
      );
    }

    const registration = registerPromotionCandidate(
      opts.promotionRegistry,
      opts.config,
      checkpointDir,
    );
    if (!registration.record) {
      return buildFailedTrainingResult(
        opts.config.backend,
        opts.start,
        "Failed to register trained artifact in promotion lifecycle",
        checkpointDir,
      );
    }

    const persistedRecord = evaluatePromotionState(
      opts.promotionRegistry,
      opts.promotionEngine,
      registration.artifactId,
      execResult.metrics,
    );
    if (!persistedRecord) {
      return buildFailedTrainingResult(
        opts.config.backend,
        opts.start,
        "Promotion lifecycle record disappeared after evaluation",
        checkpointDir,
      );
    }

    const artifact: PublishedArtifact = publishTrainingArtifact({
      artifactId: registration.artifactId,
      config: resolution.resolvedConfig,
      checkpointDir,
      datasetSize: resolution.datasetSize,
      heldOutSize: resolution.heldOutSize,
      metrics: execResult.metrics,
      record: persistedRecord,
    });

    return {
      status: "completed",
      backend: opts.config.backend,
      checkpointDir,
      artifact,
      durationMs: performance.now() - opts.start,
    };
  } catch (err) {
    return buildFailedTrainingResult(
      opts.config.backend,
      opts.start,
      err instanceof Error ? err.message : String(err),
    );
  }
}
