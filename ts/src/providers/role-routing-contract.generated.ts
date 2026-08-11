/* eslint-disable */
// AUTO-GENERATED from docs/role-routing-contract.json — DO NOT EDIT.
// Regenerate with: node scripts/generate-role-routing-contract.mjs
// CI gate: node scripts/generate-role-routing-contract.mjs --check

import type { StringSettingKey } from "./role-routing.js";

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
  architect: ["frontier", "local"],
  coach: ["mid_tier", "local"],
  competitor: ["frontier", "local"],
  curator: ["fast", "local"],
  translator: ["fast", "local"],
} as const;

// Capability ordering, so a role's requirement can be compared against what an
// endpoint declares. Only the API-backed classes are ranked: "local" names an
// artifact slot rather than a capability, and "code_policy" is not model-backed.
export const CAPABILITY_RANK: Record<string, number> = {
  fast: 0,
  frontier: 2,
  mid_tier: 1,
};

// What a distilled local artifact is treated as being capable of. Declared rather
// than assumed, because it is the value that decides which roles an artifact may
// serve once eligibility is derived instead of hardcoded.
export const LOCAL_ARTIFACT_CAPABILITY = "frontier";

// Conservative hosting fallback for endpoints without an explicit declaration.
// Endpoint settings override this transport-based value because generic transports
// such as openai-compatible may be local and vllm may be hosted remotely.
export const PROVIDER_HOSTING: Record<string, string> = {
  agent_sdk: "remote",
  anthropic: "remote",
  deterministic: "local",
  mlx: "local",
  ollama: "local",
  openai: "remote",
  "openai-compatible": "remote",
  openclaw: "remote",
  vllm: "local",
};

// Model id to send when the user has configured none and the provider is not one
// whose defaults are preserved. Declared once here because AC-912 shipped this
// table in Python only, and the TypeScript engine went on sending Claude ids to
// every self-hosted endpoint with nothing to catch it.
export const PROVIDER_DEFAULT_MODEL: Record<string, string> = {
  ollama: "llama3.1",
  openai: "gpt-4o",
  "openai-compatible": "gpt-4o",
  openrouter: "anthropic/claude-sonnet-4",
  orcarouter: "openai/gpt-5.5",
  vllm: "default",
};

// Providers whose shipped model defaults must never be rewritten.
export const MODEL_DEFAULT_PRESERVED_PROVIDERS = ["anthropic"] as const;

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
// Typed against `StringSettingKey` so a contract entry naming a field TypeScript
// does not have fails to compile here, rather than silently dropping that setting
// when a test replays a shared fixture.
export const SETTINGS_KEY_MAP: Record<string, StringSettingKey> = {
  agent_provider: "agentProvider",
  analyst_provider: "analystProvider",
  analyst_provider_capability: "analystProviderCapability",
  analyst_provider_hosting: "analystProviderHosting",
  architect_provider: "architectProvider",
  architect_provider_capability: "architectProviderCapability",
  architect_provider_hosting: "architectProviderHosting",
  coach_provider: "coachProvider",
  coach_provider_capability: "coachProviderCapability",
  coach_provider_hosting: "coachProviderHosting",
  competitor_provider: "competitorProvider",
  competitor_provider_capability: "competitorProviderCapability",
  competitor_provider_hosting: "competitorProviderHosting",
  local_model: "localModel",
  mlx_model_path: "mlxModelPath",
  model_analyst: "modelAnalyst",
  model_architect: "modelArchitect",
  model_coach: "modelCoach",
  model_competitor: "modelCompetitor",
  model_curator: "modelCurator",
  model_translator: "modelTranslator",
  provider_capability: "providerCapability",
  provider_hosting: "providerHosting",
  role_routing: "roleRouting",
  tier_haiku_model: "tierHaikuModel",
  tier_opus_model: "tierOpusModel",
  tier_sonnet_model: "tierSonnetModel",
};
