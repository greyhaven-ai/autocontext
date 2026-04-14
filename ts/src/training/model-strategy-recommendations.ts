import type {
  AdapterType,
  FamilyRecommendation,
} from "./model-strategy-types.js";

export const DEFAULT_BASE_MODEL = "Qwen/Qwen3-0.6B";
export const DEFAULT_ADAPTER_TYPE: AdapterType = "lora";
export const SMALL_DATASET = 500;
export const LARGE_DATASET = 20_000;

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

export const KNOWN_BASE_MODELS: Record<string, { parameterCount: number; supportedBackends: string[] }> = {
  "Qwen/Qwen3-0.6B": { parameterCount: 600_000_000, supportedBackends: ["cuda", "mlx"] },
  "meta-llama/Llama-3.2-1B": { parameterCount: 1_000_000_000, supportedBackends: ["cuda"] },
  "microsoft/phi-4-mini": { parameterCount: 3_800_000_000, supportedBackends: ["cuda"] },
};
