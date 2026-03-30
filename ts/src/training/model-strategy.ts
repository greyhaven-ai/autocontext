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

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type TrainingMode = "from_scratch" | "adapter_finetune" | "full_finetune";
export type AdapterType = "lora" | "qlora" | "prefix_tuning";
export type TaskComplexity = "structured" | "mixed" | "language_heavy";
export type BudgetTier = "low" | "medium" | "high";

export const TRAINING_MODES: readonly TrainingMode[] = ["from_scratch", "adapter_finetune", "full_finetune"];

export interface ModelStrategy {
  trainingMode: TrainingMode;
  baseModel?: string;
  adapterType?: AdapterType;
  reasoning: string;
  estimatedParameterCount?: number;
  estimatedTrainingTimeMinutes?: number;
}

export interface SelectionInput {
  family: string;
  datasetSize: number;
  taskComplexity?: TaskComplexity;
  budgetTier?: BudgetTier;
  deploymentTarget?: string;
  trainingModeOverride?: TrainingMode;
  baseModelOverride?: string;
}

export interface DistillationConfig {
  scenario: string;
  family: string;
  strategy: ModelStrategy;
  datasetPath: string;
  heldOutPath?: string;
  outputDir: string;
  backend?: string;
}

export interface DistilledArtifactMetadata {
  artifactId: string;
  scenario: string;
  family: string;
  trainingMode: TrainingMode;
  baseModel?: string;
  adapterType?: AdapterType;
  parameterCount: number;
  adapterParameterCount?: number;
  datasetSize: number;
  heldOutSize: number;
  trainedAt: string;
  backend?: string;
  provenance?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Default recommendations per family
// ---------------------------------------------------------------------------

interface FamilyRecommendation {
  trainingMode: TrainingMode;
  baseModel?: string;
  adapterType?: AdapterType;
  reasoning: string;
}

const DEFAULT_BASE_MODEL = "Qwen/Qwen3-0.6B";
const DEFAULT_ADAPTER_TYPE: AdapterType = "lora";
export const DEFAULT_RECOMMENDATIONS: Record<string, FamilyRecommendation> = {
  game: {
    trainingMode: "from_scratch",
    reasoning: "Game scenarios have structured, compact strategy spaces — small from-scratch models are efficient and fast to train.",
  },
  agent_task: {
    trainingMode: "adapter_finetune",
    baseModel: DEFAULT_BASE_MODEL,
    adapterType: "lora",
    reasoning: "Agent tasks require language understanding — adapter fine-tuning on a pretrained base captures instruction-following cheaply.",
  },
  simulation: {
    trainingMode: "adapter_finetune",
    baseModel: DEFAULT_BASE_MODEL,
    adapterType: "lora",
    reasoning: "Simulation scenarios benefit from pretrained reasoning but have structured action spaces suitable for lightweight adapters.",
  },
  investigation: {
    trainingMode: "adapter_finetune",
    baseModel: DEFAULT_BASE_MODEL,
    adapterType: "lora",
    reasoning: "Investigation requires evidence reasoning — a pretrained base with adapter captures diagnostic patterns efficiently.",
  },
  workflow: {
    trainingMode: "adapter_finetune",
    baseModel: DEFAULT_BASE_MODEL,
    adapterType: "lora",
    reasoning: "Workflow scenarios need sequential reasoning — adapter on a pretrained base is the best cost/quality tradeoff.",
  },
  negotiation: {
    trainingMode: "adapter_finetune",
    baseModel: DEFAULT_BASE_MODEL,
    adapterType: "lora",
    reasoning: "Negotiation requires opponent modeling and language — adapter on a pretrained base captures strategic reasoning.",
  },
  artifact_editing: {
    trainingMode: "adapter_finetune",
    baseModel: DEFAULT_BASE_MODEL,
    adapterType: "lora",
    reasoning: "Artifact editing requires code/config understanding — adapter fine-tuning on a code-aware base is most effective.",
  },
  schema_evolution: {
    trainingMode: "adapter_finetune",
    baseModel: DEFAULT_BASE_MODEL,
    adapterType: "lora",
    reasoning: "Schema evolution needs data-structure reasoning — lightweight adapter captures migration patterns.",
  },
  tool_fragility: {
    trainingMode: "adapter_finetune",
    baseModel: DEFAULT_BASE_MODEL,
    adapterType: "lora",
    reasoning: "Tool fragility requires API contract understanding — adapter on a pretrained base is efficient.",
  },
  operator_loop: {
    trainingMode: "adapter_finetune",
    baseModel: DEFAULT_BASE_MODEL,
    adapterType: "lora",
    reasoning: "Operator-loop scenarios need judgment — language model base with adapter captures escalation patterns.",
  },
  coordination: {
    trainingMode: "adapter_finetune",
    baseModel: DEFAULT_BASE_MODEL,
    adapterType: "lora",
    reasoning: "Coordination requires multi-context reasoning — adapter on a pretrained base captures handoff patterns.",
  },
};

// ---------------------------------------------------------------------------
// Selector
// ---------------------------------------------------------------------------

// Dataset size thresholds for mode escalation
const SMALL_DATASET = 500;
const LARGE_DATASET = 20_000;

export class ModelStrategySelector {
  select(input: SelectionInput): ModelStrategy {
    // Explicit overrides take priority
    if (input.trainingModeOverride || input.baseModelOverride) {
      return this.applyOverrides(input);
    }

    // Start from family recommendation
    const rec = DEFAULT_RECOMMENDATIONS[input.family];
    if (!rec) {
      return {
        trainingMode: "adapter_finetune",
        baseModel: DEFAULT_BASE_MODEL,
        adapterType: DEFAULT_ADAPTER_TYPE,
        reasoning: `No specific recommendation for family '${input.family}' — defaulting to adapter fine-tune.`,
      };
    }

    // Adjust based on dataset size and complexity
    let mode = rec.trainingMode;
    let baseModel = rec.baseModel;
    let adapterType = rec.adapterType;
    let reasoning = rec.reasoning;

    const complexity = input.taskComplexity ?? "mixed";
    const budget = input.budgetTier ?? "medium";

    // Small structured data → from_scratch even if recommendation says otherwise
    if (input.datasetSize < SMALL_DATASET && complexity === "structured") {
      mode = "from_scratch";
      baseModel = undefined;
      adapterType = undefined;
      reasoning = `Dataset size (${input.datasetSize}) is small and task is structured — from-scratch is efficient.`;
    }

    // Large dataset + high budget → escalate to full_finetune
    if (input.datasetSize > LARGE_DATASET && budget === "high" && mode !== "from_scratch") {
      mode = "full_finetune";
      adapterType = undefined;
      reasoning = `Large dataset (${input.datasetSize}) with high budget — full fine-tune maximizes quality.`;
    }

    // Language-heavy tasks on small dataset → still use adapter (don't downgrade to from_scratch)
    if (complexity === "language_heavy" && mode === "from_scratch") {
      mode = "adapter_finetune";
      baseModel = baseModel ?? DEFAULT_BASE_MODEL;
      adapterType = DEFAULT_ADAPTER_TYPE;
      reasoning = `Language-heavy task — pretrained base with adapter captures linguistic patterns even with small dataset.`;
    }

    return { trainingMode: mode, baseModel, adapterType, reasoning };
  }

  private applyOverrides(input: SelectionInput): ModelStrategy {
    const rec = DEFAULT_RECOMMENDATIONS[input.family] ?? {
      trainingMode: "adapter_finetune" as TrainingMode,
      baseModel: DEFAULT_BASE_MODEL,
      adapterType: DEFAULT_ADAPTER_TYPE,
      reasoning: "Default with overrides",
    };

    const mode = input.trainingModeOverride ?? rec.trainingMode;
    const baseModel = mode === "from_scratch"
      ? undefined
      : (input.baseModelOverride ?? rec.baseModel ?? DEFAULT_BASE_MODEL);
    const adapterType = mode === "adapter_finetune"
      ? (rec.adapterType ?? DEFAULT_ADAPTER_TYPE)
      : undefined;

    return {
      trainingMode: mode,
      baseModel,
      adapterType,
      reasoning: `Operator override: mode=${mode}${input.baseModelOverride ? `, base=${input.baseModelOverride}` : ""}.`,
    };
  }
}
