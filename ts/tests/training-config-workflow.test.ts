import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  countJsonlRecords,
  resolveTrainingConfig,
} from "../src/training/training-config-workflow.js";
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
