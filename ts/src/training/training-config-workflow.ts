import { existsSync, readFileSync } from "node:fs";

import { ModelStrategySelector } from "./model-strategy.js";
import { resolveTrainingScaleMetadata, validateTrainingScaleMetadata } from "./training-scale.js";
import type { TrainingConfig } from "./training-types.js";

export function countJsonlRecords(path: string): number {
  const content = readFileSync(path, "utf-8");
  return content.trim().split("\n").filter(Boolean).length;
}

export function resolveTrainingConfig(config: TrainingConfig): {
  resolvedConfig: TrainingConfig;
  datasetSize: number;
  heldOutSize: number;
  error?: string;
} {
  if (!existsSync(config.datasetPath)) {
    return {
      resolvedConfig: config,
      datasetSize: 0,
      heldOutSize: 0,
      error: `Dataset not found: ${config.datasetPath}`,
    };
  }

  const datasetSize = countJsonlRecords(config.datasetPath);
  const heldOutSize =
    config.heldOutPath && existsSync(config.heldOutPath)
      ? countJsonlRecords(config.heldOutPath)
      : 0;
  const scaleError = validateTrainingScaleMetadata(resolveTrainingScaleMetadata(config));
  if (scaleError) {
    return {
      resolvedConfig: config,
      datasetSize,
      heldOutSize,
      error: scaleError,
    };
  }

  if (config.backend !== "opd" && config.opdPressureMode && config.opdPressureMode !== "full_kl") {
    return {
      resolvedConfig: config,
      datasetSize,
      heldOutSize,
      error: "--opd-pressure-mode only supports the opd backend",
    };
  }

  const selector = new ModelStrategySelector();
  const strategy = selector.select({
    family: config.family,
    datasetSize,
    trainingModeOverride: config.trainingMode,
    baseModelOverride: config.baseModel,
  });

  const resolvedConfig: TrainingConfig = {
    ...config,
    trainingMode: strategy.trainingMode,
    baseModel: strategy.baseModel,
    adapterType: config.adapterType ?? strategy.adapterType,
    opdPressureMode: config.opdPressureMode ?? "full_kl",
  };

  if (resolvedConfig.trainingMode !== "from_scratch" && !resolvedConfig.baseModel) {
    return {
      resolvedConfig,
      datasetSize,
      heldOutSize,
      error: `Training mode '${resolvedConfig.trainingMode}' requires a base model`,
    };
  }

  if (resolvedConfig.baseModel) {
    const validation = selector.validateBaseModel(resolvedConfig.baseModel, config.backend);
    if (!validation.valid) {
      return {
        resolvedConfig,
        datasetSize,
        heldOutSize,
        error: validation.warnings.join("; "),
      };
    }
  }

  return { resolvedConfig, datasetSize, heldOutSize };
}
