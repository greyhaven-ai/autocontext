import { describe, expect, it } from "vitest";

import {
  DEFAULT_RECOMMENDATIONS,
  KNOWN_BASE_MODELS,
  LARGE_DATASET,
  SMALL_DATASET,
} from "../src/training/model-strategy-recommendations.js";
import {
  applyModelStrategyOverrides,
  selectModelStrategy,
  validateKnownBaseModel,
} from "../src/training/model-strategy-selection-workflow.js";
import { TRAINING_MODES } from "../src/training/model-strategy-types.js";

describe("model strategy selection workflow", () => {
  it("exposes training modes, recommendations, model registry, and thresholds", () => {
    expect(TRAINING_MODES).toEqual(["from_scratch", "adapter_finetune", "full_finetune"]);
    expect(DEFAULT_RECOMMENDATIONS.game.trainingMode).toBe("from_scratch");
    expect(DEFAULT_RECOMMENDATIONS.agent_task.trainingMode).toBe("adapter_finetune");
    expect(KNOWN_BASE_MODELS["Qwen/Qwen3-0.6B"]?.supportedBackends).toContain("cuda");
    expect(SMALL_DATASET).toBe(500);
    expect(LARGE_DATASET).toBe(20_000);
  });

  it("validates base models and operator overrides", () => {
    expect(validateKnownBaseModel("unknown/model").valid).toBe(false);
    expect(validateKnownBaseModel("Qwen/Qwen3-0.6B", "mlx")).toEqual({ valid: true, warnings: [] });
    expect(validateKnownBaseModel("Qwen/Qwen3-0.6B", "bogus").warnings[0]).toContain("may not be compatible");

    expect(applyModelStrategyOverrides({
      family: "game",
      datasetSize: 100,
      trainingModeOverride: "adapter_finetune",
    })).toMatchObject({
      trainingMode: "adapter_finetune",
      baseModel: "Qwen/Qwen3-0.6B",
      adapterType: "lora",
    });

    expect(applyModelStrategyOverrides({
      family: "agent_task",
      datasetSize: 1000,
      baseModelOverride: "meta-llama/Llama-3.2-1B",
    }).baseModel).toBe("meta-llama/Llama-3.2-1B");
  });

  it("selects strategies from family, dataset size, budget, and complexity", () => {
    expect(selectModelStrategy({
      family: "game",
      datasetSize: 100,
      taskComplexity: "structured",
    })).toMatchObject({ trainingMode: "from_scratch", baseModel: undefined });

    expect(selectModelStrategy({
      family: "agent_task",
      datasetSize: 5000,
      taskComplexity: "language_heavy",
    })).toMatchObject({ trainingMode: "adapter_finetune", adapterType: "lora" });

    expect(selectModelStrategy({
      family: "agent_task",
      datasetSize: 50_000,
      taskComplexity: "language_heavy",
      budgetTier: "high",
    })).toMatchObject({ trainingMode: "full_finetune" });

    expect(selectModelStrategy({
      family: "unknown_family",
      datasetSize: 1000,
    })).toMatchObject({ trainingMode: "adapter_finetune", baseModel: "Qwen/Qwen3-0.6B" });
  });
});
