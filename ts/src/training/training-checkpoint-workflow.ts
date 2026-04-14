import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import type { ModelRecord } from "./promotion.js";
import type {
  PublishedArtifact,
  TrainingConfig,
  TrainingExecutor,
} from "./training-types.js";
import type { TrainingBackend } from "./training-backend-core.js";

export const defaultExecutor: TrainingExecutor = async (config, checkpointDir) => {
  writeFileSync(
    join(checkpointDir, "checkpoint_info.json"),
    JSON.stringify({
      backend: config.backend,
      trainingMode: config.trainingMode,
      baseModel: config.baseModel,
      status: "trained",
      note: "Default executor — replace with real PyTorch/MLX training for production use",
      timestamp: new Date().toISOString(),
    }, null, 2),
    "utf-8",
  );
  return { success: true, metrics: { epochs: config.maxEpochs ?? 3 } };
};

export function ensureCheckpointDir(
  outputDir: string,
  backend: TrainingBackend,
  scenario: string,
): string {
  const checkpointDir = join(outputDir, backend.defaultCheckpointDir(scenario));
  if (!existsSync(checkpointDir)) {
    mkdirSync(checkpointDir, { recursive: true });
  }
  return checkpointDir;
}

export function writeTrainingManifest(
  checkpointDir: string,
  config: TrainingConfig,
  datasetSize: number,
  heldOutSize: number,
): void {
  writeFileSync(
    join(checkpointDir, "training_manifest.json"),
    JSON.stringify({
      scenario: config.scenario,
      family: config.family,
      backend: config.backend,
      trainingMode: config.trainingMode,
      baseModel: config.baseModel ?? null,
      adapterType: config.adapterType ?? null,
      datasetPath: config.datasetPath,
      datasetSize,
      heldOutSize,
      maxEpochs: config.maxEpochs ?? 3,
      batchSize: config.batchSize ?? 4,
      learningRate: config.learningRate ?? 5e-5,
      startedAt: new Date().toISOString(),
    }, null, 2),
    "utf-8",
  );
}

export function publishTrainingArtifact(opts: {
  artifactId: string;
  config: TrainingConfig;
  checkpointDir: string;
  datasetSize: number;
  heldOutSize: number;
  metrics?: Record<string, number>;
  record: ModelRecord;
}): PublishedArtifact {
  const artifact: PublishedArtifact = {
    artifactId: opts.artifactId,
    scenario: opts.config.scenario,
    family: opts.config.family,
    backend: opts.config.backend,
    trainingMode: opts.config.trainingMode,
    baseModel: opts.config.baseModel,
    adapterType: opts.config.adapterType,
    checkpointDir: opts.checkpointDir,
    datasetSize: opts.datasetSize,
    heldOutSize: opts.heldOutSize,
    trainedAt: new Date().toISOString(),
    metrics: opts.metrics,
    activationState: opts.record.activationState,
    promotionHistory: [...opts.record.promotionHistory],
  };

  writeFileSync(join(opts.checkpointDir, "artifact.json"), JSON.stringify(artifact, null, 2), "utf-8");
  writeFileSync(join(opts.checkpointDir, "promotion_state.json"), JSON.stringify(opts.record, null, 2), "utf-8");
  return artifact;
}
