import { describe, expect, it, vi } from "vitest";

import {
  executeTrainCommandWorkflow,
  TRAIN_HELP_TEXT,
  planTrainCommand,
  renderTrainSuccess,
} from "../src/cli/train-command-workflow.js";

describe("train command workflow", () => {
  it("exposes stable help text", () => {
    expect(TRAIN_HELP_TEXT).toContain("autoctx train");
    expect(TRAIN_HELP_TEXT).toContain("--scenario");
    expect(TRAIN_HELP_TEXT).toContain("--dataset");
    expect(TRAIN_HELP_TEXT).toContain("--backend");
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
          backend: "mlx",
          mode: "adapter_finetune",
          "base-model": "qwen",
          output: "artifacts",
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
      backend: "mlx",
      trainingMode: "adapter_finetune",
      baseModel: "qwen",
      json: true,
    });
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
        backend: "cuda",
        trainingMode: "from_scratch",
        baseModel: undefined,
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
      backend: "cuda",
      trainingMode: "from_scratch",
      baseModel: undefined,
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
    ).toEqual([
      "Training completed: artifact-1",
      "  Backend: cuda",
      "  Checkpoint: /tmp/checkpoint",
      "  Duration: 1.2s",
    ].join("\n"));
  });
});
