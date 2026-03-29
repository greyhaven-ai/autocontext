/**
 * AC-459: Base model selection and adapter strategy for scenario-local distillation.
 *
 * Tests the model selection layer that maps scenario families + dataset
 * characteristics to base model choices and training modes.
 */

import { describe, it, expect } from "vitest";
import {
  ModelStrategySelector,
  type TrainingMode,
  type ModelStrategy,
  type DistillationConfig,
  type DistilledArtifactMetadata,
  TRAINING_MODES,
  DEFAULT_RECOMMENDATIONS,
} from "../src/training/model-strategy.js";

// ---------------------------------------------------------------------------
// Training modes
// ---------------------------------------------------------------------------

describe("training modes", () => {
  it("defines all supported modes", () => {
    expect(TRAINING_MODES).toContain("from_scratch");
    expect(TRAINING_MODES).toContain("adapter_finetune");
    expect(TRAINING_MODES).toContain("full_finetune");
  });
});

// ---------------------------------------------------------------------------
// Default recommendations
// ---------------------------------------------------------------------------

describe("default recommendations", () => {
  it("recommends from_scratch for game scenarios", () => {
    expect(DEFAULT_RECOMMENDATIONS.game.trainingMode).toBe("from_scratch");
  });

  it("recommends adapter_finetune for agent_task scenarios", () => {
    expect(DEFAULT_RECOMMENDATIONS.agent_task.trainingMode).toBe("adapter_finetune");
  });

  it("has recommendations for all major families", () => {
    const families = ["game", "agent_task", "simulation", "investigation"];
    for (const f of families) {
      expect(DEFAULT_RECOMMENDATIONS[f]).toBeDefined();
      expect(DEFAULT_RECOMMENDATIONS[f].trainingMode).toBeTruthy();
    }
  });
});

// ---------------------------------------------------------------------------
// ModelStrategySelector
// ---------------------------------------------------------------------------

describe("ModelStrategySelector", () => {
  const selector = new ModelStrategySelector();

  it("selects from_scratch for small structured game datasets", () => {
    const strategy = selector.select({
      family: "game",
      datasetSize: 100,
      taskComplexity: "structured",
    });

    expect(strategy.trainingMode).toBe("from_scratch");
    expect(strategy.baseModel).toBeUndefined();
    expect(strategy.reasoning).toBeTruthy();
  });

  it("selects adapter_finetune for language-heavy agent tasks", () => {
    const strategy = selector.select({
      family: "agent_task",
      datasetSize: 5000,
      taskComplexity: "language_heavy",
    });

    expect(strategy.trainingMode).toBe("adapter_finetune");
    expect(strategy.baseModel).toBeTruthy();
    expect(strategy.adapterType).toBe("lora");
  });

  it("selects full_finetune for large datasets with budget", () => {
    const strategy = selector.select({
      family: "agent_task",
      datasetSize: 50000,
      taskComplexity: "language_heavy",
      budgetTier: "high",
    });

    expect(strategy.trainingMode).toBe("full_finetune");
    expect(strategy.baseModel).toBeTruthy();
  });

  it("respects explicit training mode override", () => {
    const strategy = selector.select({
      family: "game",
      datasetSize: 100,
      taskComplexity: "structured",
      trainingModeOverride: "adapter_finetune",
    });

    expect(strategy.trainingMode).toBe("adapter_finetune");
  });

  it("respects explicit base model override", () => {
    const strategy = selector.select({
      family: "agent_task",
      datasetSize: 1000,
      baseModelOverride: "meta-llama/Llama-3.2-1B",
    });

    expect(strategy.baseModel).toBe("meta-llama/Llama-3.2-1B");
  });
});

// ---------------------------------------------------------------------------
// DistillationConfig
// ---------------------------------------------------------------------------

describe("DistillationConfig", () => {
  it("captures full config for a training run", () => {
    const config: DistillationConfig = {
      scenario: "grid_ctf",
      family: "game",
      strategy: {
        trainingMode: "from_scratch",
        reasoning: "Small structured game",
      },
      datasetPath: "/path/to/train.jsonl",
      heldOutPath: "/path/to/held_out.jsonl",
      outputDir: "/path/to/output",
    };

    expect(config.strategy.trainingMode).toBe("from_scratch");
    expect(config.datasetPath).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Artifact metadata
// ---------------------------------------------------------------------------

describe("DistilledArtifactMetadata", () => {
  it("records base model and adapter strategy", () => {
    const meta: DistilledArtifactMetadata = {
      artifactId: "model_001",
      scenario: "code_review",
      family: "agent_task",
      trainingMode: "adapter_finetune",
      baseModel: "Qwen/Qwen3-0.6B",
      adapterType: "lora",
      parameterCount: 600_000_000,
      adapterParameterCount: 2_000_000,
      datasetSize: 5000,
      heldOutSize: 500,
      trainedAt: "2026-03-28T10:00:00Z",
    };

    expect(meta.trainingMode).toBe("adapter_finetune");
    expect(meta.baseModel).toBe("Qwen/Qwen3-0.6B");
    expect(meta.adapterType).toBe("lora");
    expect(meta.adapterParameterCount).toBeLessThan(meta.parameterCount);
  });

  it("from_scratch has no base model", () => {
    const meta: DistilledArtifactMetadata = {
      artifactId: "model_002",
      scenario: "grid_ctf",
      family: "game",
      trainingMode: "from_scratch",
      parameterCount: 10_000_000,
      datasetSize: 200,
      heldOutSize: 20,
      trainedAt: "2026-03-28T10:00:00Z",
    };

    expect(meta.baseModel).toBeUndefined();
    expect(meta.adapterType).toBeUndefined();
  });
});
