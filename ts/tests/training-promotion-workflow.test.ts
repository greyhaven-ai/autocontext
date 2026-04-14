import { describe, expect, it } from "vitest";

import { ModelRegistry, PromotionEngine } from "../src/training/promotion.js";
import {
  evaluatePromotionState,
  registerPromotionCandidate,
} from "../src/training/training-promotion-workflow.js";
import { buildFailedTrainingResult } from "../src/training/training-result-workflow.js";
import { readMetric } from "../src/training/training-metric-utils.js";
import type { TrainingConfig } from "../src/training/training-types.js";

describe("training promotion workflow", () => {
  it("registers candidates, evaluates promotion transitions, and reads metric aliases", () => {
    const config: TrainingConfig = {
      scenario: "grid_ctf",
      family: "agent_task",
      datasetPath: "/tmp/train.jsonl",
      outputDir: "/tmp/output",
      backend: "cuda",
      trainingMode: "adapter_finetune",
      baseModel: "Qwen/Qwen3-0.6B",
    };
    const registry = new ModelRegistry();
    const engine = new PromotionEngine();

    expect(readMetric({ held_out_score: 0.95 }, "heldOutScore", "held_out_score")).toBe(0.95);

    const registration = registerPromotionCandidate(registry, config, "/tmp/output/models/grid_ctf/cuda");
    expect(registration.record?.activationState).toBe("candidate");

    const persistedRecord = evaluatePromotionState(registry, engine, registration.artifactId, {
      heldOutScore: 0.95,
      incumbentScore: 1.0,
      parseFailureRate: 0,
      validationFailureRate: 0,
    });
    expect(persistedRecord?.activationState).toBe("shadow");
    expect(persistedRecord?.promotionHistory[0]?.to).toBe("shadow");
  });

  it("builds stable failed training results", () => {
    const failed = buildFailedTrainingResult("cuda", 0, "boom", "/tmp/output");
    expect(failed).toMatchObject({
      status: "failed",
      backend: "cuda",
      checkpointDir: "/tmp/output",
      error: "boom",
    });
    expect(typeof failed.durationMs).toBe("number");
  });
});
