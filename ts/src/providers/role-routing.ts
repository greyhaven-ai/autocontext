import { SUPPORTED_PROVIDER_TYPES } from "./supported-provider-types.js";

export const PROVIDER_CLASSES = ["frontier", "mid_tier", "fast", "local", "code_policy"] as const;

export type ProviderClass = (typeof PROVIDER_CLASSES)[number];

export const ROUTED_GENERATION_ROLES = [
  "competitor",
  "analyst",
  "coach",
  "architect",
  "curator",
  "translator",
] as const;

export type GenerationRole = (typeof ROUTED_GENERATION_ROLES)[number];

export const ROLE_ROUTING_MODES = ["off", "auto"] as const;

export type RoleRoutingMode = (typeof ROLE_ROUTING_MODES)[number];

export const PROVIDER_CLASS_COST_PER_1K_TOKENS: Partial<Record<ProviderClass, number>> = {
  frontier: 0.015,
  mid_tier: 0.003,
  fast: 0.001,
  local: 0.0,
};

export const DEFAULT_ROLE_ROUTING_TABLE = {
  competitor: ["frontier", "local"],
  analyst: ["mid_tier", "local"],
  coach: ["mid_tier", "local"],
  architect: ["frontier"],
  curator: ["fast"],
  translator: ["fast", "local"],
} as const satisfies Record<GenerationRole, readonly ProviderClass[]>;

export const LOCAL_ELIGIBLE_ROLES = [
  "competitor",
  "analyst",
  "coach",
  "translator",
] as const satisfies readonly GenerationRole[];

export const EXPLICIT_PROVIDER_CLASS: Record<string, ProviderClass> = {
  anthropic: "frontier",
  mlx: "local",
  openclaw: "frontier",
  deterministic: "fast",
  agent_sdk: "frontier",
  openai: "mid_tier",
  "openai-compatible": "mid_tier",
  ollama: "mid_tier",
  vllm: "mid_tier",
};

const DEFAULT_ROLE_MODELS: Record<GenerationRole, string> = {
  competitor: "claude-sonnet-4-5-20250929",
  analyst: "claude-sonnet-4-5-20250929",
  coach: "claude-opus-4-6",
  architect: "claude-opus-4-6",
  curator: "claude-opus-4-6",
  translator: "claude-sonnet-4-5-20250929",
};

export interface RoleRoutingSettings {
  agentProvider: string;
  roleRouting?: string;
  competitorProvider?: string;
  analystProvider?: string;
  coachProvider?: string;
  architectProvider?: string;
  modelCompetitor?: string;
  modelAnalyst?: string;
  modelCoach?: string;
  modelArchitect?: string;
  modelCurator?: string;
  modelTranslator?: string;
  tierOpusModel?: string;
  tierSonnetModel?: string;
  tierHaikuModel?: string;
  mlxModelPath?: string;
}

export interface RoleRoutingContext {
  availableLocalModels?: readonly string[];
  /**
   * Explicit provider override (e.g. the CLI `--provider` flag, `RunManager({ providerType })`,
   * or a `switch_provider` command). Takes precedence over `settings.agentProvider` for any
   * role that doesn't already have its own role-specific provider setting (which stays the
   * highest-priority route). When omitted, routing falls back to the env-derived
   * `settings.agentProvider`, unchanged from prior behavior.
   */
  providerOverride?: string;
}

export interface RoutedProviderConfig {
  role: string;
  providerType: string;
  providerClass: ProviderClass;
  model: string;
  estimatedCostPer1kTokens: number;
  executableInTypeScript: boolean;
  unsupportedReason?: string;
}

export interface RoleRoutingCostEstimate {
  roles: Partial<Record<GenerationRole, RoutedProviderConfig>>;
  totalPer1kTokens: number;
  allFrontierPer1kTokens: number;
  savingsVsAllFrontier: number;
}

function clean(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

// Mirrors provider-config-resolution.ts's envProviderType precedence: the
// AUTOCONTEXT_AGENT_PROVIDER/AUTOCONTEXT_PROVIDER env vars outrank an explicit
// providerOverride, matching how resolveProviderConfig() resolves the default provider.
function envProviderOverride(): string | undefined {
  return clean(process.env.AUTOCONTEXT_AGENT_PROVIDER) ?? clean(process.env.AUTOCONTEXT_PROVIDER);
}

function normalizeProvider(providerType: string | undefined): string {
  return (clean(providerType) ?? "anthropic").toLowerCase();
}

function roleSpecificProvider(role: string, settings: RoleRoutingSettings): string | undefined {
  switch (role) {
    case "competitor":
      return clean(settings.competitorProvider);
    case "analyst":
      return clean(settings.analystProvider);
    case "coach":
      return clean(settings.coachProvider);
    case "architect":
      return clean(settings.architectProvider);
    default:
      return undefined;
  }
}

function roleSpecificModel(role: string, settings: RoleRoutingSettings): string {
  switch (role) {
    case "competitor":
      return clean(settings.modelCompetitor) ?? DEFAULT_ROLE_MODELS.competitor;
    case "analyst":
      return clean(settings.modelAnalyst) ?? DEFAULT_ROLE_MODELS.analyst;
    case "coach":
      return clean(settings.modelCoach) ?? DEFAULT_ROLE_MODELS.coach;
    case "architect":
      return clean(settings.modelArchitect) ?? DEFAULT_ROLE_MODELS.architect;
    case "curator":
      return clean(settings.modelCurator) ?? DEFAULT_ROLE_MODELS.curator;
    case "translator":
      return clean(settings.modelTranslator) ?? DEFAULT_ROLE_MODELS.translator;
    default:
      return clean(settings.tierSonnetModel) ?? "claude-sonnet-4-5-20250929";
  }
}

function tierModel(providerClass: ProviderClass, settings: RoleRoutingSettings): string {
  switch (providerClass) {
    case "frontier":
      return clean(settings.tierOpusModel) ?? "claude-opus-4-6";
    case "mid_tier":
    case "code_policy":
      return clean(settings.tierSonnetModel) ?? "claude-sonnet-4-5-20250929";
    case "fast":
      return clean(settings.tierHaikuModel) ?? "claude-haiku-4-5-20251001";
    case "local":
      return clean(settings.mlxModelPath) ?? "local";
  }
}

function executableInTypeScript(providerType: string): boolean {
  return (SUPPORTED_PROVIDER_TYPES as readonly string[]).includes(providerType);
}

function routedConfig(
  role: string,
  providerType: string,
  providerClass: ProviderClass,
  model: string,
): RoutedProviderConfig {
  const executable = executableInTypeScript(providerType);
  return {
    role,
    providerType,
    providerClass,
    model,
    estimatedCostPer1kTokens: PROVIDER_CLASS_COST_PER_1K_TOKENS[providerClass] ?? 0.003,
    executableInTypeScript: executable,
    unsupportedReason: executable
      ? undefined
      : "TypeScript provider runtime does not support routed provider",
  };
}

export function routeRoleProvider(
  settings: RoleRoutingSettings,
  role: string,
  context: RoleRoutingContext = {},
): RoutedProviderConfig {
  const explicitProvider = roleSpecificProvider(role, settings);
  if (explicitProvider) {
    const providerType = normalizeProvider(explicitProvider);
    const providerClass = EXPLICIT_PROVIDER_CLASS[providerType] ?? "frontier";
    const model =
      providerClass === "local" ? tierModel("local", settings) : roleSpecificModel(role, settings);
    return routedConfig(role, providerType, providerClass, model);
  }

  const providerType = normalizeProvider(
    envProviderOverride() ?? clean(context.providerOverride) ?? settings.agentProvider,
  );
  const providerClass = EXPLICIT_PROVIDER_CLASS[providerType] ?? "mid_tier";

  if (settings.roleRouting !== "auto") {
    const model =
      providerClass === "local" ? tierModel("local", settings) : roleSpecificModel(role, settings);
    return routedConfig(role, providerType, providerClass, model);
  }

  const preferences = DEFAULT_ROLE_ROUTING_TABLE[role as GenerationRole] ?? ["mid_tier"];
  const hasLocal = Boolean(context.availableLocalModels?.length);
  const localModel = clean(context.availableLocalModels?.[0]) ?? clean(settings.mlxModelPath);
  if (
    hasLocal &&
    localModel &&
    (LOCAL_ELIGIBLE_ROLES as readonly string[]).includes(role) &&
    preferences.some((preference) => preference === "local")
  ) {
    return routedConfig(role, "mlx", "local", localModel);
  }

  return routedConfig(role, providerType, preferences[0], tierModel(preferences[0], settings));
}

export function estimateRoleRoutingCost(
  settings: RoleRoutingSettings,
  context: RoleRoutingContext = {},
): RoleRoutingCostEstimate {
  const roles: Partial<Record<GenerationRole, RoutedProviderConfig>> = {};
  let totalPer1kTokens = 0;

  for (const role of ROUTED_GENERATION_ROLES) {
    const routed = routeRoleProvider(settings, role, context);
    roles[role] = routed;
    totalPer1kTokens += routed.estimatedCostPer1kTokens;
  }

  const allFrontierPer1kTokens =
    ROUTED_GENERATION_ROLES.length * (PROVIDER_CLASS_COST_PER_1K_TOKENS.frontier ?? 0);
  const savingsVsAllFrontier = Math.max(0, allFrontierPer1kTokens - totalPer1kTokens);

  return {
    roles,
    totalPer1kTokens,
    allFrontierPer1kTokens,
    savingsVsAllFrontier,
  };
}
