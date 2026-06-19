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
  -o, --output <dir>       Output directory
  --opd-diagnostics        Write OPD/GKD token-pressure diagnostics
  --opd-diagnostics-debug-tokens  Include raw sampled token text in diagnostics
  --json                   Output as JSON
  -h, --help               Show this help

Notes:
  The TypeScript package requires an injected training executor for real MLX/CUDA training.
  For end-to-end local training, prefer the Python package's \`autoctx train\` command.`;

export interface TrainCommandValues {
  scenario?: string;
  family?: string;
  dataset?: string;
  "held-out"?: string;
  backend?: string;
  mode?: string;
  "base-model"?: string;
  output?: string;
  "opd-diagnostics"?: boolean;
  "opd-diagnostics-debug-tokens"?: boolean;
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
  opdDiagnostics: boolean;
  opdDiagnosticsDebugTokens: boolean;
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

  return {
    scenario: values.scenario,
    family: values.family ?? "agent_task",
    datasetPath: resolvePath(values.dataset),
    heldOutPath: values["held-out"] ? resolvePath(values["held-out"]) : undefined,
    outputDir: values.output ? resolvePath(values.output) : resolvePath(runsRoot),
    backend: values.backend ?? "cuda",
    trainingMode: (values.mode ?? "from_scratch") as
      | "from_scratch"
      | "adapter_finetune"
      | "full_finetune",
    baseModel: values["base-model"],
    opdDiagnostics: !!values["opd-diagnostics"],
    opdDiagnosticsDebugTokens: !!values["opd-diagnostics-debug-tokens"],
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
      opdDiagnostics?: boolean;
      opdDiagnosticsDebugTokens?: boolean;
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
    opdDiagnostics: opts.plan.opdDiagnostics,
    opdDiagnosticsDebugTokens: opts.plan.opdDiagnosticsDebugTokens,
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
