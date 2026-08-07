/* eslint-disable */
// AUTO-GENERATED from docs/role-routing-contract.json — DO NOT EDIT.
// Regenerate with: node scripts/generate-role-routing-contract.mjs
// CI gate: node scripts/generate-role-routing-contract.mjs --check

export const PROVIDER_CLASSES = ["frontier", "mid_tier", "fast", "local", "code_policy"] as const;

export const ROLE_ROUTING_MODES = ["off", "auto"] as const;

export const PROVIDER_CLASS_COST_PER_1K_TOKENS = {
  fast: 0.001,
  frontier: 0.015,
  local: 0,
  mid_tier: 0.003,
} as const;

export const DEFAULT_ROLE_ROUTING_TABLE = {
  analyst: ["mid_tier", "local"],
  architect: ["frontier"],
  coach: ["mid_tier", "local"],
  competitor: ["frontier", "local"],
  curator: ["fast"],
  translator: ["fast", "local"],
} as const;

export const LOCAL_ELIGIBLE_ROLES = ["analyst", "coach", "competitor", "translator"] as const;

export const EXPLICIT_PROVIDER_CLASS: Record<string, string> = {
  agent_sdk: "frontier",
  anthropic: "frontier",
  deterministic: "fast",
  mlx: "local",
  ollama: "mid_tier",
  openai: "mid_tier",
  "openai-compatible": "mid_tier",
  openclaw: "frontier",
  vllm: "mid_tier",
};
