export type TrainingShardingStrategy = "none" | "fsdp" | "deepspeed_zero3";

export interface TrainingScaleMetadata {
  deviceCount: number;
  shardingStrategy: TrainingShardingStrategy;
  memoryLimitMb: number;
  perDeviceMemoryLimitMb: number;
  baseModelParameterCount: number;
  baseModelQuantization: string;
  deploymentTargetVramMb: number;
}
