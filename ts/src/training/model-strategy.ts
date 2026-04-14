/**
 * Base model selection and adapter strategy (AC-459).
 *
 * Maps scenario families + dataset characteristics to base model choices
 * and training modes. Makes model selection an explicit operator concern
 * instead of always defaulting to from-scratch tiny models.
 *
 * Three training modes:
 * - from_scratch: train a small model from nothing (game scenarios, small datasets)
 * - adapter_finetune: LoRA/QLoRA on a pretrained base (language tasks, medium datasets)
 * - full_finetune: full parameter update on a pretrained base (large datasets, high budget)
 */

import {
  DEFAULT_RECOMMENDATIONS,
  KNOWN_BASE_MODELS,
} from "./model-strategy-recommendations.js";
import {
  selectModelStrategy,
  validateKnownBaseModel,
} from "./model-strategy-selection-workflow.js";
import type {
  DistillationConfig,
  DistilledArtifactMetadata,
  ModelStrategy,
  SelectionInput,
} from "./model-strategy-types.js";

export {
  TRAINING_MODES,
  type AdapterType,
  type BudgetTier,
  type TaskComplexity,
  type TrainingMode,
} from "./model-strategy-types.js";
export type {
  DistillationConfig,
  DistilledArtifactMetadata,
  ModelStrategy,
  SelectionInput,
} from "./model-strategy-types.js";
export {
  DEFAULT_RECOMMENDATIONS,
  KNOWN_BASE_MODELS,
} from "./model-strategy-recommendations.js";

export class ModelStrategySelector {
  validateBaseModel(modelId: string, backend?: string): { valid: boolean; warnings: string[] } {
    return validateKnownBaseModel(modelId, backend);
  }

  select(input: SelectionInput): ModelStrategy {
    return selectModelStrategy(input);
  }
}
