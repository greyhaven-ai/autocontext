export type ModelScaleProfile = {
  backend: "trl";
  trainingMode: "adapter_finetune";
  trlMode: "gkd" | "grpo";
  baseModel: string;
  teacherModel: string;
  baseModelParameterCount: number;
  baseModelQuantization: string;
  memoryLimitMb: number;
  deviceCount: number;
  shardingStrategy: "none" | "fsdp" | "deepspeed_zero3";
  perDeviceMemoryLimitMb: number;
  deploymentTargetVramMb: number;
};

export const MODEL_SCALE_PROFILES: Record<string, ModelScaleProfile> = {
  cuda_qlora_7b_rlvr: {
    backend: "trl",
    trainingMode: "adapter_finetune",
    trlMode: "grpo",
    baseModel: "Qwen/Qwen2.5-7B-Instruct",
    teacherModel: "Qwen/Qwen2.5-14B-Instruct",
    baseModelParameterCount: 7_000_000_000,
    baseModelQuantization: "nf4",
    memoryLimitMb: 24_576,
    deviceCount: 1,
    shardingStrategy: "none",
    perDeviceMemoryLimitMb: 24_576,
    deploymentTargetVramMb: 24_576,
  },
  cuda_sharded_32b_distill: {
    backend: "trl",
    trainingMode: "adapter_finetune",
    trlMode: "gkd",
    baseModel: "Qwen/Qwen2.5-32B-Instruct",
    teacherModel: "Qwen/Qwen2.5-72B-Instruct",
    baseModelParameterCount: 32_000_000_000,
    baseModelQuantization: "nf4",
    memoryLimitMb: 98_304,
    deviceCount: 4,
    shardingStrategy: "deepspeed_zero3",
    perDeviceMemoryLimitMb: 24_576,
    deploymentTargetVramMb: 24_576,
  },
};

export function listModelScaleProfiles(): string[] {
  return Object.keys(MODEL_SCALE_PROFILES).sort();
}

export function getModelScaleProfile(name: string): ModelScaleProfile {
  const profile = MODEL_SCALE_PROFILES[name];
  if (!profile) {
    throw new Error(
      `unknown model scale profile ${name}; expected one of ${listModelScaleProfiles().join(", ")}`,
    );
  }
  return { ...profile };
}
