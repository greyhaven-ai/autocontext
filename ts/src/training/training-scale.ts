import type { TrainingScaleMetadata } from "./training-scale-types.js";
import type { TrainingConfig } from "./training-types.js";

export function resolveTrainingScaleMetadata(config: TrainingConfig): TrainingScaleMetadata {
  return {
    deviceCount: config.deviceCount ?? 1,
    shardingStrategy: config.shardingStrategy ?? "none",
    memoryLimitMb: config.memoryLimitMb ?? 16_384,
    perDeviceMemoryLimitMb: config.perDeviceMemoryLimitMb ?? config.memoryLimitMb ?? 16_384,
    baseModelParameterCount: config.baseModelParameterCount ?? 0,
    baseModelQuantization: config.baseModelQuantization ?? "",
    deploymentTargetVramMb: config.deploymentTargetVramMb ?? 0,
  };
}

export function validateTrainingScaleMetadata(scale: TrainingScaleMetadata): string | null {
  if (scale.deviceCount < 1) {
    return "deviceCount must be >= 1";
  }
  if (!["none", "fsdp", "deepspeed_zero3"].includes(scale.shardingStrategy)) {
    return "shardingStrategy must be none|fsdp|deepspeed_zero3";
  }
  for (const [key, value] of Object.entries({
    memoryLimitMb: scale.memoryLimitMb,
    perDeviceMemoryLimitMb: scale.perDeviceMemoryLimitMb,
    baseModelParameterCount: scale.baseModelParameterCount,
    deploymentTargetVramMb: scale.deploymentTargetVramMb,
  })) {
    if (value < 0) {
      return `${key} must be >= 0`;
    }
  }
  return null;
}
