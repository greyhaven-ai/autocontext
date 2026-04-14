import { mkdtempSync, rmSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { BackendRegistry, TrainingBackend } from "../src/training/training-backend-core.js";
import {
  buildFailedTrainingResult,
  countJsonlRecords,
  defaultExecutor,
  ensureCheckpointDir,
  evaluatePromotionState,
  publishTrainingArtifact,
  readMetric,
  registerPromotionCandidate,
  resolveTrainingConfig,
  writeTrainingManifest,
} from "../src/training/training-runner-workflow.js";
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

describe("training runner workflow helpers", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-training-workflow-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("counts jsonl rows, resolves config, and writes manifests/checkpoints", async () => {
    const config: TrainingConfig = {
      scenario: "grid_ctf",
      family: "agent_task",
      datasetPath: join(dir, "train.jsonl"),
      outputDir: join(dir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
    };
    writeFileSync(config.datasetPath, '{"a":1}\n{"a":2}\n', "utf-8");

    expect(countJsonlRecords(config.datasetPath)).toBe(2);

    const resolution = resolveTrainingConfig(config);
    expect(resolution.error).toBeUndefined();
    expect(resolution.datasetSize).toBe(2);
    expect(resolution.resolvedConfig.baseModel).toBeTruthy();

    const checkpointDir = ensureCheckpointDir(config.outputDir, new StubBackend("cuda", true), config.scenario);
    writeTrainingManifest(checkpointDir, resolution.resolvedConfig, resolution.datasetSize, resolution.heldOutSize);
    expect(existsSync(join(checkpointDir, "training_manifest.json"))).toBe(true);

    const execResult = await defaultExecutor(resolution.resolvedConfig, checkpointDir);
    expect(execResult.success).toBe(true);
    expect(existsSync(join(checkpointDir, "checkpoint_info.json"))).toBe(true);
  });

  it("registers promotion candidates, evaluates promotion, publishes artifacts, and builds failures", () => {
    const config: TrainingConfig = {
      scenario: "grid_ctf",
      family: "agent_task",
      datasetPath: join(dir, "train.jsonl"),
      outputDir: join(dir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
      baseModel: "Qwen/Qwen3-0.6B",
    };
    const checkpointDir = ensureCheckpointDir(config.outputDir, new StubBackend("cuda", true), config.scenario);
    const registry = new ModelRegistry();
    const engine = new PromotionEngine();

    expect(readMetric({ heldOutScore: 0.95 }, "heldOutScore", "score")).toBe(0.95);

    const registration = registerPromotionCandidate(registry, config, checkpointDir);
    expect(registration.record?.activationState).toBe("candidate");

    const persisted = evaluatePromotionState(registry, engine, registration.artifactId, {
      heldOutScore: 0.95,
      incumbentScore: 1.0,
      parseFailureRate: 0,
      validationFailureRate: 0,
    });
    expect(persisted?.activationState).toBe("shadow");

    const artifact = publishTrainingArtifact({
      artifactId: registration.artifactId,
      config,
      checkpointDir,
      datasetSize: 2,
      heldOutSize: 1,
      metrics: { heldOutScore: 0.95 },
      record: persisted!,
    });
    expect(artifact.activationState).toBe("shadow");
    expect(JSON.parse(readFileSync(join(checkpointDir, "artifact.json"), "utf-8")).artifactId).toBe(artifact.artifactId);

    const failed = buildFailedTrainingResult("cuda", 0, "boom", checkpointDir);
    expect(failed).toMatchObject({ status: "failed", backend: "cuda", error: "boom" });
  });
});
