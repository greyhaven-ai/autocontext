import { getModelScaleProfile, listModelScaleProfiles } from "../training/model-defaults.js";

export const TRAIN_HELP_TEXT = `autoctx train — train a distilled model from curated dataset

Usage: autoctx train --scenario <name> --dataset <path> [options]

Options:
  -s, --scenario <name>    Scenario name (required)
  --family <name>          Scenario family (default: agent_task)
  -d, --dataset <path>     Training dataset JSONL path (required)
  --held-out <path>        Held-out evaluation JSONL path
  --backend <name>         Training backend: cuda, mlx (default: cuda)
  --mode <mode>            from_scratch, adapter_finetune, full_finetune
  --base-model <id>        Base model for adapter/full fine-tune
  --teacher-model <id>     Teacher model for TRL distillation
  --scale-profile <name>   Opt-in larger-model profile: ${listModelScaleProfiles().join(", ")}
  --memory-limit <mb>      Global training memory budget in MB
  -o, --output <dir>       Output directory
  --opd-diagnostics        Write OPD/GKD token-pressure diagnostics
  --opd-diagnostics-debug-tokens  Include raw sampled token text in diagnostics
  --opd-pressure-mode <mode>  OPD pressure mode: full_kl, sample_positive, sample_positive_reverse_negative
  --device-count <n>       Planned accelerator count for scaled training
  --sharding-strategy <s>  Sharding strategy: none, fsdp, deepspeed_zero3
  --per-device-memory-limit <mb>  Per-device memory cap for scaled training
  --base-model-parameters <n>  Base/student parameter count for registry metadata
  --base-model-quantization <q>  Base/student quantization label, e.g. nf4
  --deployment-target-vram <mb>  Deployment target VRAM cap for registry gating
  --json                   Output as JSON
  -h, --help               Show this help

Notes:
  The TypeScript package requires an injected training executor for real MLX/CUDA training.
  For end-to-end local training, prefer the Python package's \`autoctx train\` command.`;

export type OpdPressureMode = "full_kl" | "sample_positive" | "sample_positive_reverse_negative";
export type TrainingShardingStrategy = "none" | "fsdp" | "deepspeed_zero3";

export const TRAIN_COMMAND_PARSE_OPTIONS = {
  scenario: { type: "string", short: "s" },
  family: { type: "string" },
  dataset: { type: "string", short: "d" },
  "held-out": { type: "string" },
  backend: { type: "string" },
  mode: { type: "string" },
  "base-model": { type: "string" },
  "teacher-model": { type: "string" },
  "scale-profile": { type: "string" },
  "memory-limit": { type: "string" },
  output: { type: "string", short: "o" },
  "opd-diagnostics": { type: "boolean" },
  "opd-diagnostics-debug-tokens": { type: "boolean" },
  "opd-pressure-mode": { type: "string" },
  "device-count": { type: "string" },
  "sharding-strategy": { type: "string" },
  "per-device-memory-limit": { type: "string" },
  "base-model-parameters": { type: "string" },
  "base-model-quantization": { type: "string" },
  "deployment-target-vram": { type: "string" },
  json: { type: "boolean" },
  help: { type: "boolean", short: "h" },
} as const;

const OPD_PRESSURE_MODES = [
  "full_kl",
  "sample_positive",
  "sample_positive_reverse_negative",
] as const;

function parseOptionalNumber(value: number | string | undefined, name: string): number | undefined {
  if (value === undefined) {
    return undefined;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${name} must be >= 0`);
  }
  return parsed;
}

function normalizeOpdPressureMode(value: string | undefined): OpdPressureMode {
  const mode = value ?? "full_kl";
  if (!OPD_PRESSURE_MODES.includes(mode as OpdPressureMode)) {
    throw new Error(
      "--opd-pressure-mode must be full_kl|sample_positive|sample_positive_reverse_negative",
    );
  }
  return mode as OpdPressureMode;
}

export interface TrainCommandValues {
  scenario?: string;
  family?: string;
  dataset?: string;
  "held-out"?: string;
  backend?: string;
  mode?: string;
  "base-model"?: string;
  "teacher-model"?: string;
  "scale-profile"?: string;
  "memory-limit"?: number | string;
  output?: string;
  "opd-diagnostics"?: boolean;
  "opd-diagnostics-debug-tokens"?: boolean;
  "opd-pressure-mode"?: string;
  "device-count"?: number | string;
  "sharding-strategy"?: string;
  "per-device-memory-limit"?: number | string;
  "base-model-parameters"?: number | string;
  "base-model-quantization"?: string;
  "deployment-target-vram"?: number | string;
  json?: boolean;
}

export interface TrainCommandPlan {
  scenario: string;
  family: string;
  datasetPath: string;
  heldOutPath?: string;
  outputDir: string;
  backend: string;
  trainingMode: "from_scratch" | "adapter_finetune" | "full_finetune";
  baseModel?: string;
  teacherModel?: string;
  trlMode?: "gkd" | "grpo";
  opdDiagnostics: boolean;
  opdDiagnosticsDebugTokens: boolean;
  opdPressureMode: OpdPressureMode;
  memoryLimitMb?: number;
  deviceCount?: number;
  shardingStrategy?: TrainingShardingStrategy;
  perDeviceMemoryLimitMb?: number;
  baseModelParameterCount?: number;
  baseModelQuantization?: string;
  deploymentTargetVramMb?: number;
  json: boolean;
}

export function planTrainCommand(
  values: TrainCommandValues,
  runsRoot: string,
  resolvePath: (value: string) => string,
): TrainCommandPlan {
  if (!values.scenario || !values.dataset) {
    throw new Error("Error: --scenario and --dataset are required. Run 'autoctx train --help'.");
  }

  const profile = values["scale-profile"]
    ? getModelScaleProfile(values["scale-profile"])
    : undefined;
  const backend = values.backend ?? profile?.backend ?? "cuda";
  const opdPressureMode = normalizeOpdPressureMode(values["opd-pressure-mode"]);
  const shardingStrategy = (values["sharding-strategy"] ??
    profile?.shardingStrategy ??
    "none") as TrainingShardingStrategy;
  const memoryLimitMb =
    parseOptionalNumber(values["memory-limit"], "--memory-limit") ?? profile?.memoryLimitMb;
  const deviceCount =
    parseOptionalNumber(values["device-count"], "--device-count") ?? profile?.deviceCount;
  if (deviceCount !== undefined && deviceCount < 1) {
    throw new Error("--device-count must be >= 1");
  }
  if (!["none", "fsdp", "deepspeed_zero3"].includes(shardingStrategy)) {
    throw new Error("--sharding-strategy must be none|fsdp|deepspeed_zero3");
  }
  if (backend !== "opd" && opdPressureMode !== "full_kl") {
    throw new Error("--opd-pressure-mode only supports --backend opd");
  }

  return {
    scenario: values.scenario,
    family: values.family ?? "agent_task",
    datasetPath: resolvePath(values.dataset),
    heldOutPath: values["held-out"] ? resolvePath(values["held-out"]) : undefined,
    outputDir: values.output ? resolvePath(values.output) : resolvePath(runsRoot),
    backend,
    trainingMode: (values.mode ?? profile?.trainingMode ?? "from_scratch") as
      | "from_scratch"
      | "adapter_finetune"
      | "full_finetune",
    baseModel: values["base-model"] ?? profile?.baseModel,
    teacherModel: values["teacher-model"] ?? profile?.teacherModel,
    trlMode: profile?.trlMode,
    opdDiagnostics: !!values["opd-diagnostics"],
    opdDiagnosticsDebugTokens: !!values["opd-diagnostics-debug-tokens"],
    opdPressureMode,
    memoryLimitMb,
    deviceCount,
    shardingStrategy,
    perDeviceMemoryLimitMb:
      parseOptionalNumber(values["per-device-memory-limit"], "--per-device-memory-limit") ??
      profile?.perDeviceMemoryLimitMb,
    baseModelParameterCount:
      parseOptionalNumber(values["base-model-parameters"], "--base-model-parameters") ??
      profile?.baseModelParameterCount,
    baseModelQuantization: values["base-model-quantization"] ?? profile?.baseModelQuantization,
    deploymentTargetVramMb:
      parseOptionalNumber(values["deployment-target-vram"], "--deployment-target-vram") ??
      profile?.deploymentTargetVramMb,
    json: !!values.json,
  };
}

export async function executeTrainCommandWorkflow<TResult extends { status?: string }>(opts: {
  plan: TrainCommandPlan;
  createRunner: () => {
    usesSyntheticExecutor(): boolean;
    train(request: {
      scenario: string;
      family: string;
      datasetPath: string;
      heldOutPath?: string;
      outputDir: string;
      backend: string;
      trainingMode: "from_scratch" | "adapter_finetune" | "full_finetune";
      baseModel?: string;
      teacherModel?: string;
      trlMode?: "gkd" | "grpo";
      opdDiagnostics?: boolean;
      opdDiagnosticsDebugTokens?: boolean;
      opdPressureMode?: OpdPressureMode;
      memoryLimitMb?: number;
      deviceCount?: number;
      shardingStrategy?: TrainingShardingStrategy;
      perDeviceMemoryLimitMb?: number;
      baseModelParameterCount?: number;
      baseModelQuantization?: string;
      deploymentTargetVramMb?: number;
    }): Promise<TResult>;
  };
}): Promise<TResult> {
  const runner = opts.createRunner();
  if (runner.usesSyntheticExecutor()) {
    throw new Error(
      "Training failed: no real training executor is configured in the TypeScript package. Use the Python package's 'autoctx train' command or inject a TrainingRunner executor via the package API.",
    );
  }
  return runner.train({
    scenario: opts.plan.scenario,
    family: opts.plan.family,
    datasetPath: opts.plan.datasetPath,
    heldOutPath: opts.plan.heldOutPath,
    outputDir: opts.plan.outputDir,
    backend: opts.plan.backend,
    trainingMode: opts.plan.trainingMode,
    baseModel: opts.plan.baseModel,
    teacherModel: opts.plan.teacherModel,
    trlMode: opts.plan.trlMode,
    opdDiagnostics: opts.plan.opdDiagnostics,
    opdDiagnosticsDebugTokens: opts.plan.opdDiagnosticsDebugTokens,
    opdPressureMode: opts.plan.opdPressureMode,
    memoryLimitMb: opts.plan.memoryLimitMb,
    deviceCount: opts.plan.deviceCount,
    shardingStrategy: opts.plan.shardingStrategy,
    perDeviceMemoryLimitMb: opts.plan.perDeviceMemoryLimitMb,
    baseModelParameterCount: opts.plan.baseModelParameterCount,
    baseModelQuantization: opts.plan.baseModelQuantization,
    deploymentTargetVramMb: opts.plan.deploymentTargetVramMb,
  });
}

export function renderTrainSuccess(result: {
  artifact?: { artifactId?: string } | null;
  backend: string;
  checkpointDir?: string | null;
  durationMs: number;
}): string {
  return [
    `Training completed: ${result.artifact?.artifactId}`,
    `  Backend: ${result.backend}`,
    `  Checkpoint: ${result.checkpointDir}`,
    `  Duration: ${(result.durationMs / 1000).toFixed(1)}s`,
  ].join("\n");
}
