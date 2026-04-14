import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { BackendRegistry, TrainingBackend } from "../src/training/training-backend-core.js";
import { executeTrainingRunWorkflow } from "../src/training/training-run-execution-workflow.js";
import { ModelRegistry, PromotionEngine } from "../src/training/promotion.js";
import type { TrainingConfig } from "../src/training/training-types.js";

class StubBackend extends TrainingBackend {
  constructor(
    readonly name: string,
    private readonly available: boolean,
  ) {
    super();
  }

  isAvailable(): boolean {
    return this.available;
  }

  defaultCheckpointDir(scenario: string): string {
    return join("models", scenario, this.name);
  }
}

describe("training run execution workflow", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-training-run-execution-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("completes the training run and publishes artifacts", async () => {
    const registry = new BackendRegistry();
    registry.register(new StubBackend("cuda", true));
    const config: TrainingConfig = {
      scenario: "workflow_success",
      family: "agent_task",
      datasetPath: join(dir, "train.jsonl"),
      outputDir: join(dir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
    };
    writeFileSync(config.datasetPath, '{"conversations":[{"from":"human","value":"hi"}]}\n', "utf-8");

    const result = await executeTrainingRunWorkflow({
      start: 0,
      config,
      registry,
      executor: async () => ({ success: true, metrics: { heldOutScore: 0.95, incumbentScore: 1.0 } }),
      promotionRegistry: new ModelRegistry(),
      promotionEngine: new PromotionEngine(),
    });

    expect(result.status).toBe("completed");
    expect(result.artifact?.activationState).toBe("shadow");
    expect(existsSync(join(result.checkpointDir!, "training_manifest.json"))).toBe(true);
    expect(JSON.parse(readFileSync(join(result.checkpointDir!, "artifact.json"), "utf-8"))).toMatchObject({
      scenario: "workflow_success",
      backend: "cuda",
    });
  });

  it("returns stable failures for unknown backends and executor failures", async () => {
    const missingBackendResult = await executeTrainingRunWorkflow({
      start: 0,
      config: {
        scenario: "missing_backend",
        family: "game",
        datasetPath: join(dir, "train.jsonl"),
        outputDir: join(dir, "output"),
        backend: "bogus",
        trainingMode: "from_scratch",
      },
      registry: new BackendRegistry(),
      executor: async () => ({ success: true, metrics: {} }),
      promotionRegistry: new ModelRegistry(),
      promotionEngine: new PromotionEngine(),
    });
    expect(missingBackendResult).toMatchObject({
      status: "failed",
      error: "Unknown training backend: bogus",
    });

    const registry = new BackendRegistry();
    registry.register(new StubBackend("cuda", true));
    const config: TrainingConfig = {
      scenario: "executor_failure",
      family: "agent_task",
      datasetPath: join(dir, "train.jsonl"),
      outputDir: join(dir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
    };
    writeFileSync(config.datasetPath, '{"conversations":[{"from":"human","value":"hi"}]}\n', "utf-8");

    const executorFailure = await executeTrainingRunWorkflow({
      start: 0,
      config,
      registry,
      executor: async () => ({ success: false, error: "Training executor returned failure" }),
      promotionRegistry: new ModelRegistry(),
      promotionEngine: new PromotionEngine(),
    });
    expect(executorFailure).toMatchObject({
      status: "failed",
      backend: "cuda",
      error: "Training executor returned failure",
    });
  });
});
