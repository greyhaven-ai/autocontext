import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ModelRegistry } from "../src/training/promotion.js";
import {
  defaultExecutor,
  ensureCheckpointDir,
  publishTrainingArtifact,
  writeTrainingManifest,
} from "../src/training/training-checkpoint-workflow.js";
import { TrainingBackend } from "../src/training/training-backend-core.js";
import type { TrainingConfig } from "../src/training/training-types.js";

class StubBackend extends TrainingBackend {
  constructor(readonly name: string) {
    super();
  }

  isAvailable(): boolean {
    return true;
  }

  defaultCheckpointDir(scenario: string): string {
    return join("models", scenario, this.name);
  }
}

describe("training checkpoint workflow", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-training-checkpoint-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("creates checkpoint dirs, manifests, executor outputs, and artifact files", async () => {
    const config: TrainingConfig = {
      scenario: "grid_ctf",
      family: "agent_task",
      datasetPath: join(dir, "train.jsonl"),
      outputDir: join(dir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
      baseModel: "Qwen/Qwen3-0.6B",
    };

    const checkpointDir = ensureCheckpointDir(config.outputDir, new StubBackend("cuda"), config.scenario);
    expect(existsSync(checkpointDir)).toBe(true);

    writeTrainingManifest(checkpointDir, config, 2, 1);
    expect(JSON.parse(readFileSync(join(checkpointDir, "training_manifest.json"), "utf-8"))).toMatchObject({
      scenario: "grid_ctf",
      datasetSize: 2,
      heldOutSize: 1,
    });

    const executorResult = await defaultExecutor(config, checkpointDir);
    expect(executorResult).toMatchObject({ success: true, metrics: { epochs: 3 } });
    expect(existsSync(join(checkpointDir, "checkpoint_info.json"))).toBe(true);

    const registry = new ModelRegistry();
    const artifactId = registry.register({
      scenario: config.scenario,
      family: config.family,
      backend: config.backend,
      checkpointDir,
      activationState: "candidate",
    });
    const artifact = publishTrainingArtifact({
      artifactId,
      config,
      checkpointDir,
      datasetSize: 2,
      heldOutSize: 1,
      metrics: { heldOutScore: 0.91 },
      record: registry.get(artifactId)!,
    });

    expect(artifact).toMatchObject({
      artifactId,
      activationState: "candidate",
      datasetSize: 2,
      heldOutSize: 1,
    });
    expect(existsSync(join(checkpointDir, "artifact.json"))).toBe(true);
    expect(existsSync(join(checkpointDir, "promotion_state.json"))).toBe(true);
  });
});
