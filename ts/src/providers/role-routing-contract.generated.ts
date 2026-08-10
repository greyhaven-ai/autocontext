/* eslint-disable */
// AUTO-GENERATED from docs/role-routing-contract.json — DO NOT EDIT.
// Regenerate with: node scripts/generate-role-routing-contract.mjs
// CI gate: node scripts/generate-role-routing-contract.mjs --check

import type { RoleRoutingSettings } from "./role-routing.js";

export const PROVIDER_CLASSES = ["frontier", "mid_tier", "fast", "local", "code_policy"] as const;

export type ProviderClass = (typeof PROVIDER_CLASSES)[number];

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

// Kept in the contract's own declaration order, like the per-role preference arrays
// above: order is semantically meaningful for neither, but this makes the two
// consistent, and a committed contract file needs no sort for determinism.
export const LOCAL_ELIGIBLE_ROLES = ["competitor", "analyst", "coach", "translator"] as const;

// Typed against ProviderClass (not Record<string, string>) so a contract value that
// isn't a declared provider class fails to compile here, instead of surfacing later as
// a mistyped ProviderClass deep inside routing logic.
export const EXPLICIT_PROVIDER_CLASS: Record<string, ProviderClass> = {
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

// Python settings key -> the RoleRoutingSettings field holding the same value.
// Typed against `keyof RoleRoutingSettings` so a contract entry naming a field
// TypeScript does not have fails to compile here, rather than silently dropping
// that setting when a test replays a shared fixture.
export const SETTINGS_KEY_MAP: Record<string, keyof RoleRoutingSettings> = {
  agent_provider: "agentProvider",
  analyst_provider: "analystProvider",
  architect_provider: "architectProvider",
  coach_provider: "coachProvider",
  competitor_provider: "competitorProvider",
  local_model: "localModel",
  mlx_model_path: "mlxModelPath",
  model_analyst: "modelAnalyst",
  model_architect: "modelArchitect",
  model_coach: "modelCoach",
  model_competitor: "modelCompetitor",
  model_curator: "modelCurator",
  model_translator: "modelTranslator",
  role_routing: "roleRouting",
  tier_haiku_model: "tierHaikuModel",
  tier_opus_model: "tierOpusModel",
  tier_sonnet_model: "tierSonnetModel",
};
