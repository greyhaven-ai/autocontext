import {
  DEFAULT_ADAPTER_TYPE,
  DEFAULT_BASE_MODEL,
  DEFAULT_RECOMMENDATIONS,
  KNOWN_BASE_MODELS,
  LARGE_DATASET,
  SMALL_DATASET,
} from "./model-strategy-recommendations.js";
import type {
  ModelStrategy,
  SelectionInput,
  TrainingMode,
} from "./model-strategy-types.js";

export function validateKnownBaseModel(
  modelId: string,
  backend?: string,
): { valid: boolean; warnings: string[] } {
  const warnings: string[] = [];
  const known = KNOWN_BASE_MODELS[modelId];
  if (!known) {
    warnings.push(`Base model '${modelId}' is not in the known model registry — verify it exists and is downloadable`);
  } else if (backend && !known.supportedBackends.includes(backend)) {
    warnings.push(`Base model '${modelId}' may not be compatible with backend '${backend}'`);
  }
  return { valid: warnings.length === 0, warnings };
}

export function applyModelStrategyOverrides(input: SelectionInput): ModelStrategy {
  const recommendation = DEFAULT_RECOMMENDATIONS[input.family] ?? {
    trainingMode: "adapter_finetune" as TrainingMode,
    baseModel: DEFAULT_BASE_MODEL,
    adapterType: DEFAULT_ADAPTER_TYPE,
    reasoning: "Default with overrides",
  };

  const trainingMode = input.trainingModeOverride ?? recommendation.trainingMode;
  const baseModel = trainingMode === "from_scratch"
    ? undefined
    : (input.baseModelOverride ?? recommendation.baseModel ?? DEFAULT_BASE_MODEL);
  const adapterType = trainingMode === "adapter_finetune"
    ? (recommendation.adapterType ?? DEFAULT_ADAPTER_TYPE)
    : undefined;

  return {
    trainingMode,
    baseModel,
    adapterType,
    reasoning: `Operator override: mode=${trainingMode}${input.baseModelOverride ? `, base=${input.baseModelOverride}` : ""}.`,
  };
}

export function selectModelStrategy(input: SelectionInput): ModelStrategy {
  if (input.trainingModeOverride || input.baseModelOverride) {
    return applyModelStrategyOverrides(input);
  }

  const recommendation = DEFAULT_RECOMMENDATIONS[input.family];
  if (!recommendation) {
    return {
      trainingMode: "adapter_finetune",
      baseModel: DEFAULT_BASE_MODEL,
      adapterType: DEFAULT_ADAPTER_TYPE,
      reasoning: `No specific recommendation for family '${input.family}' — defaulting to adapter fine-tune.`,
    };
  }

  let trainingMode = recommendation.trainingMode;
  let baseModel = recommendation.baseModel;
  let adapterType = recommendation.adapterType;
  let reasoning = recommendation.reasoning;

  const complexity = input.taskComplexity ?? "mixed";
  const budget = input.budgetTier ?? "medium";

  if (input.datasetSize < SMALL_DATASET && complexity === "structured") {
    trainingMode = "from_scratch";
    baseModel = undefined;
    adapterType = undefined;
    reasoning = `Dataset size (${input.datasetSize}) is small and task is structured — from-scratch is efficient.`;
  }

  if (input.datasetSize > LARGE_DATASET && budget === "high" && trainingMode !== "from_scratch") {
    trainingMode = "full_finetune";
    adapterType = undefined;
    reasoning = `Large dataset (${input.datasetSize}) with high budget — full fine-tune maximizes quality.`;
  }

  if (complexity === "language_heavy" && trainingMode === "from_scratch") {
    trainingMode = "adapter_finetune";
    baseModel = baseModel ?? DEFAULT_BASE_MODEL;
    adapterType = DEFAULT_ADAPTER_TYPE;
    reasoning = "Language-heavy task — pretrained base with adapter captures linguistic patterns even with small dataset.";
  }

  return { trainingMode, baseModel, adapterType, reasoning };
}
