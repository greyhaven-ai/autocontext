import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  countJsonlRecords,
  resolveTrainingConfig,
} from "../src/training/training-config-workflow.js";
import { resolveTrainingScaleMetadata } from "../src/training/training-scale.js";
import type { TrainingConfig } from "../src/training/training-types.js";

describe("training config workflow", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-training-config-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("counts dataset rows and resolves strategy defaults", () => {
    const config: TrainingConfig = {
      scenario: "grid_ctf",
      family: "agent_task",
      datasetPath: join(dir, "train.jsonl"),
      heldOutPath: join(dir, "held_out.jsonl"),
      outputDir: join(dir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
    };
    writeFileSync(config.datasetPath, '{"a":1}\n{"a":2}\n', "utf-8");
    writeFileSync(config.heldOutPath!, '{"a":3}\n', "utf-8");

    expect(countJsonlRecords(config.datasetPath)).toBe(2);

    const resolution = resolveTrainingConfig(config);
    expect(resolution.error).toBeUndefined();
    expect(resolution.datasetSize).toBe(2);
    expect(resolution.heldOutSize).toBe(1);
    expect(resolution.resolvedConfig.baseModel).toBeTruthy();
    expect(resolution.resolvedConfig.adapterType).toBeTruthy();
    expect(resolveTrainingScaleMetadata(resolution.resolvedConfig)).toMatchObject({
      deviceCount: 1,
      shardingStrategy: "none",
      memoryLimitMb: 16_384,
    });
  });

  it("preserves profile base models when resolving adapter training", () => {
    const config: TrainingConfig = {
      scenario: "grid_ctf",
      family: "agent_task",
      datasetPath: join(dir, "train.jsonl"),
      outputDir: join(dir, "output"),
      backend: "trl",
      trainingMode: "adapter_finetune",
      baseModel: "Qwen/Qwen2.5-7B-Instruct",
    };
    writeFileSync(config.datasetPath, '{"a":1}\n', "utf-8");

    const resolution = resolveTrainingConfig(config);

    expect(resolution.error).toBeUndefined();
    expect(resolution.resolvedConfig.trainingMode).toBe("adapter_finetune");
    expect(resolution.resolvedConfig.baseModel).toBe("Qwen/Qwen2.5-7B-Instruct");
  });

  it("rejects invalid training scale fields", () => {
    const config: TrainingConfig = {
      scenario: "bad-scale",
      family: "agent_task",
      datasetPath: join(dir, "train.jsonl"),
      outputDir: join(dir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
      deviceCount: 0,
    };
    writeFileSync(config.datasetPath, '{"a":1}\n', "utf-8");

    expect(resolveTrainingConfig(config).error).toBe("deviceCount must be >= 1");
  });

  it("returns stable errors for missing datasets and invalid base models", () => {
    const missingDataset = resolveTrainingConfig({
      scenario: "missing",
      family: "game",
      datasetPath: join(dir, "missing.jsonl"),
      outputDir: join(dir, "output"),
      backend: "cuda",
      trainingMode: "from_scratch",
    });
    expect(missingDataset.error).toContain("Dataset not found:");

    const config: TrainingConfig = {
      scenario: "bad-model",
      family: "agent_task",
      datasetPath: join(dir, "train.jsonl"),
      outputDir: join(dir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
      baseModel: "unknown/model",
    };
    writeFileSync(config.datasetPath, '{"a":1}\n', "utf-8");

    const invalidBaseModel = resolveTrainingConfig(config);
    expect(invalidBaseModel.error).toContain("known model registry");
  });
});
