import { parseArgs } from "node:util";
import { describe, expect, it, vi } from "vitest";

import {
  executeTrainCommandWorkflow,
  TRAIN_COMMAND_PARSE_OPTIONS,
  TRAIN_HELP_TEXT,
  planTrainCommand,
  renderTrainSuccess,
} from "../src/cli/train-command-workflow.js";

describe("train command workflow", () => {
  it("exposes stable help text", () => {
    expect(TRAIN_HELP_TEXT).toContain("autoctx train");
    expect(TRAIN_HELP_TEXT).toContain("`autoctx train` command");
    expect(TRAIN_HELP_TEXT).not.toContain("\u0000");
    expect(TRAIN_HELP_TEXT).toContain("--scenario");
    expect(TRAIN_HELP_TEXT).toContain("--dataset");
    expect(TRAIN_HELP_TEXT).toContain("--backend");
    expect(TRAIN_HELP_TEXT).toContain("--opd-diagnostics");
    expect(TRAIN_HELP_TEXT).toContain("--opd-diagnostics-debug-tokens");
    expect(TRAIN_HELP_TEXT).toContain("--opd-pressure-mode");
    expect(TRAIN_HELP_TEXT).toContain("--device-count");
    expect(TRAIN_HELP_TEXT).toContain("--sharding-strategy");
    expect(TRAIN_HELP_TEXT).toContain("--per-device-memory-limit");
    expect(TRAIN_HELP_TEXT).toContain("--base-model-parameters");
    expect(TRAIN_HELP_TEXT).toContain("--base-model-quantization");
    expect(TRAIN_HELP_TEXT).toContain("--deployment-target-vram");
    expect(TRAIN_HELP_TEXT).toContain("--scale-profile");
  });

  it("requires scenario and dataset", () => {
    expect(() =>
      planTrainCommand(
        {
          scenario: undefined,
          family: undefined,
          dataset: undefined,
          "held-out": undefined,
          backend: undefined,
          mode: undefined,
          "base-model": undefined,
          output: undefined,
          json: false,
        },
        "/tmp/runs",
        (value: string) => `/abs/${value}`,
      ),
    ).toThrow("Error: --scenario and --dataset are required. Run 'autoctx train --help'.");
  });

  it("plans train command options", () => {
    expect(
      planTrainCommand(
        {
          scenario: "grid_ctf",
          family: "agent_task",
          dataset: "train.jsonl",
          "held-out": "heldout.jsonl",
          backend: "opd",
          mode: "adapter_finetune",
          "base-model": "qwen",
          output: "artifacts",
          "opd-diagnostics": true,
          "opd-diagnostics-debug-tokens": true,
          "opd-pressure-mode": "sample_positive",
          "device-count": 4,
          "sharding-strategy": "fsdp",
          "per-device-memory-limit": 16_384,
          "base-model-parameters": 32_000_000_000,
          "base-model-quantization": "nf4",
          "deployment-target-vram": 16_384,
          json: true,
        },
        "/tmp/runs",
        (value: string) => `/abs/${value}`,
      ),
    ).toEqual({
      scenario: "grid_ctf",
      family: "agent_task",
      datasetPath: "/abs/train.jsonl",
      heldOutPath: "/abs/heldout.jsonl",
      outputDir: "/abs/artifacts",
      backend: "opd",
      trainingMode: "adapter_finetune",
      baseModel: "qwen",
      opdDiagnostics: true,
      opdDiagnosticsDebugTokens: true,
      opdPressureMode: "sample_positive",
      memoryLimitMb: undefined,
      deviceCount: 4,
      shardingStrategy: "fsdp",
      perDeviceMemoryLimitMb: 16_384,
      baseModelParameterCount: 32_000_000_000,
      baseModelQuantization: "nf4",
      deploymentTargetVramMb: 16_384,
      json: true,
    });
  });

  it("applies larger-model scale profiles", () => {
    expect(
      planTrainCommand(
        {
          scenario: "grid_ctf",
          dataset: "train.jsonl",
          "scale-profile": "cuda_sharded_32b_distill",
        },
        "/tmp/runs",
        (value: string) => `/abs/${value}`,
      ),
    ).toMatchObject({
      backend: "trl",
      trainingMode: "adapter_finetune",
      baseModel: "Qwen/Qwen2.5-32B-Instruct",
      teacherModel: "Qwen/Qwen2.5-72B-Instruct",
      trlMode: "gkd",
      deviceCount: 4,
      shardingStrategy: "deepspeed_zero3",
      baseModelQuantization: "nf4",
      memoryLimitMb: 98_304,
      deploymentTargetVramMb: 24_576,
    });
  });

  it("keeps scale-profile defaults through the real parseArgs path", () => {
    const { values } = parseArgs({
      args: ["--scenario", "grid_ctf", "--dataset", "train.jsonl", "--scale-profile", "cuda_qlora_7b_rlvr"],
      options: TRAIN_COMMAND_PARSE_OPTIONS,
    });

    const plan = planTrainCommand(values, "/tmp/runs", (value: string) => `/abs/${value}`);

    expect(values.backend).toBeUndefined();
    expect(values.mode).toBeUndefined();
    expect(plan).toMatchObject({
      backend: "trl",
      trainingMode: "adapter_finetune",
      baseModel: "Qwen/Qwen2.5-7B-Instruct",
      trlMode: "grpo",
    });
  });

  it("rejects non-default OPD pressure modes for non-OPD backends", () => {
    expect(() =>
      planTrainCommand(
        {
          scenario: "grid_ctf",
          dataset: "train.jsonl",
          backend: "cuda",
          "opd-pressure-mode": "sample_positive",
        },
        "/tmp/runs",
        (value: string) => `/abs/${value}`,
      ),
    ).toThrow("--opd-pressure-mode only supports --backend opd");
  });

  it("fails clearly when only the synthetic executor is available", async () => {
    await expect(
      executeTrainCommandWorkflow({
        plan: {
          scenario: "grid_ctf",
          family: "agent_task",
          datasetPath: "/abs/train.jsonl",
          heldOutPath: undefined,
          outputDir: "/tmp/runs",
          backend: "cuda",
          trainingMode: "from_scratch",
          baseModel: undefined,
          opdDiagnostics: false,
          opdDiagnosticsDebugTokens: false,
          opdPressureMode: "full_kl",
          json: false,
        },
        createRunner: () => ({
          usesSyntheticExecutor: () => true,
          train: vi.fn(),
        }),
      }),
    ).rejects.toThrow(
      "Training failed: no real training executor is configured in the TypeScript package. Use the Python package's 'autoctx train' command or inject a TrainingRunner executor via the package API.",
    );
  });

  it("executes train workflow with planned request", async () => {
    const train = vi.fn().mockResolvedValue({
      status: "completed",
      backend: "cuda",
      durationMs: 1234,
      artifact: { artifactId: "artifact-1" },
      checkpointDir: "/tmp/checkpoint",
    });

    const result = await executeTrainCommandWorkflow({
      plan: {
        scenario: "grid_ctf",
        family: "agent_task",
        datasetPath: "/abs/train.jsonl",
        heldOutPath: "/abs/heldout.jsonl",
        outputDir: "/tmp/runs",
        backend: "opd",
        trainingMode: "from_scratch",
        baseModel: undefined,
        teacherModel: "Qwen/Qwen2.5-14B-Instruct",
        trlMode: "grpo",
        opdDiagnostics: true,
        opdDiagnosticsDebugTokens: true,
        opdPressureMode: "sample_positive_reverse_negative",
        deviceCount: 2,
        shardingStrategy: "deepspeed_zero3",
        deploymentTargetVramMb: 24_576,
        json: false,
      },
      createRunner: () => ({
        usesSyntheticExecutor: () => false,
        train,
      }),
    });

    expect(train).toHaveBeenCalledWith({
      scenario: "grid_ctf",
      family: "agent_task",
      datasetPath: "/abs/train.jsonl",
      heldOutPath: "/abs/heldout.jsonl",
      outputDir: "/tmp/runs",
      backend: "opd",
      trainingMode: "from_scratch",
      baseModel: undefined,
      teacherModel: "Qwen/Qwen2.5-14B-Instruct",
      trlMode: "grpo",
      opdDiagnostics: true,
      opdDiagnosticsDebugTokens: true,
      opdPressureMode: "sample_positive_reverse_negative",
      memoryLimitMb: undefined,
      deviceCount: 2,
      shardingStrategy: "deepspeed_zero3",
      deploymentTargetVramMb: 24_576,
    });
    expect(result).toMatchObject({ status: "completed", backend: "cuda" });
  });

  it("renders human-readable train success output", () => {
    expect(
      renderTrainSuccess({
        artifact: { artifactId: "artifact-1" },
        backend: "cuda",
        checkpointDir: "/tmp/checkpoint",
        durationMs: 1234,
      }),
    ).toEqual(
      [
        "Training completed: artifact-1",
        "  Backend: cuda",
        "  Checkpoint: /tmp/checkpoint",
        "  Duration: 1.2s",
      ].join("\n"),
    );
  });
});
