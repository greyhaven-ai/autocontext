import type { ActivationState, PromotionEvent } from "./promotion.js";
import type { TrainingMode } from "./model-strategy.js";

export interface TrainingConfig {
  scenario: string;
  family: string;
  datasetPath: string;
  heldOutPath?: string;
  outputDir: string;
  backend: string;
  trainingMode: TrainingMode;
  baseModel?: string;
  adapterType?: string;
  maxEpochs?: number;
  batchSize?: number;
  learningRate?: number;
}

export interface PublishedArtifact {
  artifactId: string;
  scenario: string;
  family: string;
  backend: string;
  trainingMode: TrainingMode;
  baseModel?: string;
  adapterType?: string;
  checkpointDir: string;
  datasetSize: number;
  heldOutSize: number;
  trainedAt: string;
  metrics?: Record<string, number>;
  activationState: ActivationState;
  promotionHistory: PromotionEvent[];
}

export interface TrainingResult {
  status: "completed" | "failed";
  backend: string;
  checkpointDir?: string;
  artifact?: PublishedArtifact;
  durationMs: number;
  error?: string;
}

export type TrainingExecutor = (
  config: TrainingConfig,
  checkpointDir: string,
) => Promise<{
  success: boolean;
  metrics?: Record<string, number>;
  error?: string;
}>;
