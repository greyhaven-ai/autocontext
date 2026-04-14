export type TrainingMode = "from_scratch" | "adapter_finetune" | "full_finetune";
export type AdapterType = "lora" | "qlora" | "prefix_tuning";
export type TaskComplexity = "structured" | "mixed" | "language_heavy";
export type BudgetTier = "low" | "medium" | "high";

export const TRAINING_MODES: readonly TrainingMode[] = [
  "from_scratch",
  "adapter_finetune",
  "full_finetune",
];

export interface ModelStrategy {
  trainingMode: TrainingMode;
  baseModel?: string;
  adapterType?: AdapterType;
  reasoning: string;
  estimatedParameterCount?: number;
  estimatedTrainingTimeMinutes?: number;
}

export interface SelectionInput {
  family: string;
  datasetSize: number;
  taskComplexity?: TaskComplexity;
  budgetTier?: BudgetTier;
  deploymentTarget?: string;
  trainingModeOverride?: TrainingMode;
  baseModelOverride?: string;
}

export interface DistillationConfig {
  scenario: string;
  family: string;
  strategy: ModelStrategy;
  datasetPath: string;
  heldOutPath?: string;
  outputDir: string;
  backend?: string;
}

export interface DistilledArtifactMetadata {
  artifactId: string;
  scenario: string;
  family: string;
  trainingMode: TrainingMode;
  baseModel?: string;
  adapterType?: AdapterType;
  parameterCount: number;
  adapterParameterCount?: number;
  datasetSize: number;
  heldOutSize: number;
  trainedAt: string;
  backend?: string;
  provenance?: Record<string, unknown>;
}

export interface FamilyRecommendation {
  trainingMode: TrainingMode;
  baseModel?: string;
  adapterType?: AdapterType;
  reasoning: string;
}
