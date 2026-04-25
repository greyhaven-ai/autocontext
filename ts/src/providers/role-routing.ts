import { SUPPORTED_PROVIDER_TYPES } from "./supported-provider-types.js";

export const PROVIDER_CLASSES = [
  "frontier",
  "mid_tier",
  "fast",
  "local",
  "code_policy",
] as const;

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

const DEFAULT_ROLE_MODELS = {
  competitor: "claude-sonnet-4-5-20250929",
  analyst: "claude-sonnet-4-5-20250929",
  coach: "claude-opus-4-6",
  architect: "claude-opus-4-6",
  curator: "claude-opus-4-6",
  translator: "claude-sonnet-4-5-20250929",
} as const satisfies Record<GenerationRole, string>;

const DEFAULT_TIER_MODELS = {
  frontier: "claude-opus-4-6",
  mid_tier: "claude-sonnet-4-5-20250929",
  fast: "claude-haiku-4-5-20251001",
  local: "",
} as const satisfies Record<Exclude<ProviderClass, "code_policy">, string>;

export interface RoleRoutingSettings {
  agentProvider: string;
  roleRouting?: RoleRoutingMode | string;
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
  generation?: number;
  retryCount?: number;
  isPlateau?: boolean;
  availableLocalModels?: readonly string[];
  scenarioName?: string;
}

export interface RoutedProviderConfig {
  providerType: string;
  model?: string;
  providerClass: ProviderClass;
  estimatedCostPer1kTokens: number;
  executableInTypeScript: boolean;
  unsupportedReason?: string;
}

export interface RoleRoutingCostEstimate {
  totalPer1kTokens: number;
  allFrontierPer1kTokens: number;
  savingsVsAllFrontier: number;
  roles: Record<GenerationRole, {
    providerClass: ProviderClass;
    providerType: string;
    costPer1kTokens: number;
  }>;
}

type RoutingTable = Record<string, readonly ProviderClass[]>;

function normalizeOptional(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function providerClassCost(providerClass: ProviderClass): number {
  return PROVIDER_CLASS_COST_PER_1K_TOKENS[providerClass] ?? 0.003;
}

function roleModelForSettings(settings: RoleRoutingSettings, role: string): string | undefined {
  switch (role) {
    case "competitor":
      return normalizeOptional(settings.modelCompetitor) ?? DEFAULT_ROLE_MODELS.competitor;
    case "analyst":
      return normalizeOptional(settings.modelAnalyst) ?? DEFAULT_ROLE_MODELS.analyst;
    case "coach":
      return normalizeOptional(settings.modelCoach) ?? DEFAULT_ROLE_MODELS.coach;
    case "architect":
      return normalizeOptional(settings.modelArchitect) ?? DEFAULT_ROLE_MODELS.architect;
    case "curator":
      return normalizeOptional(settings.modelCurator) ?? DEFAULT_ROLE_MODELS.curator;
    case "translator":
      return normalizeOptional(settings.modelTranslator) ?? DEFAULT_ROLE_MODELS.translator;
    default:
      return undefined;
  }
}

function roleProviderOverrideForSettings(
  settings: RoleRoutingSettings,
  role: string,
): string | undefined {
  switch (role) {
    case "competitor":
      return normalizeOptional(settings.competitorProvider);
    case "analyst":
      return normalizeOptional(settings.analystProvider);
    case "coach":
      return normalizeOptional(settings.coachProvider);
    case "architect":
      return normalizeOptional(settings.architectProvider);
    default:
      return undefined;
  }
}

function tierModelForClass(
  settings: RoleRoutingSettings,
  providerClass: ProviderClass,
): string | undefined {
  switch (providerClass) {
    case "frontier":
      return normalizeOptional(settings.tierOpusModel) ?? DEFAULT_TIER_MODELS.frontier;
    case "mid_tier":
      return normalizeOptional(settings.tierSonnetModel) ?? DEFAULT_TIER_MODELS.mid_tier;
    case "fast":
      return normalizeOptional(settings.tierHaikuModel) ?? DEFAULT_TIER_MODELS.fast;
    case "local":
      return normalizeOptional(settings.mlxModelPath) ?? undefined;
    case "code_policy":
      return undefined;
  }
}

function providerClassForExplicitProvider(providerType: string): ProviderClass {
  return EXPLICIT_PROVIDER_CLASS[providerType.trim().toLowerCase()] ?? "frontier";
}

function providerClassForDefaultProvider(providerType: string): ProviderClass {
  return EXPLICIT_PROVIDER_CLASS[providerType.trim().toLowerCase()] ?? "mid_tier";
}

function executableInTypeScript(providerType: string): boolean {
  return (SUPPORTED_PROVIDER_TYPES as readonly string[]).includes(providerType.trim().toLowerCase());
}

function routedConfig(opts: {
  providerType: string;
  providerClass: ProviderClass;
  model?: string;
}): RoutedProviderConfig {
  const providerType = opts.providerType.trim().toLowerCase();
  const executable = executableInTypeScript(providerType);
  return {
    providerType,
    model: normalizeOptional(opts.model),
    providerClass: opts.providerClass,
    estimatedCostPer1kTokens: providerClassCost(opts.providerClass),
    executableInTypeScript: executable,
    ...(executable
      ? {}
      : {
          unsupportedReason:
            `TypeScript provider runtime does not support routed provider ${JSON.stringify(providerType)}`,
        }),
  };
}

function configForProviderClass(
  settings: RoleRoutingSettings,
  role: string,
  providerClass: ProviderClass,
  localModelPath?: string,
): RoutedProviderConfig {
  if (providerClass === "local") {
    return routedConfig({
      providerType: "mlx",
      providerClass,
      model: localModelPath ?? tierModelForClass(settings, providerClass),
    });
  }

  return routedConfig({
    providerType: settings.agentProvider,
    providerClass,
    model: tierModelForClass(settings, providerClass) ?? roleModelForSettings(settings, role),
  });
}

function configForExplicitProvider(
  settings: RoleRoutingSettings,
  role: string,
  providerType: string,
): RoutedProviderConfig {
  const providerClass = providerClassForExplicitProvider(providerType);
  return routedConfig({
    providerType,
    providerClass,
    model: providerClass === "local"
      ? tierModelForClass(settings, "local")
      : roleModelForSettings(settings, role),
  });
}

function configForDefaultProvider(
  settings: RoleRoutingSettings,
  role: string,
): RoutedProviderConfig {
  const providerClass = providerClassForDefaultProvider(settings.agentProvider);
  return routedConfig({
    providerType: settings.agentProvider,
    providerClass,
    model: providerClass === "local"
      ? tierModelForClass(settings, "local")
      : roleModelForSettings(settings, role),
  });
}

function localRoutingIsAvailable(
  role: string,
  context: RoleRoutingContext,
): context is RoleRoutingContext & { availableLocalModels: readonly [string, ...string[]] } {
  return (
    (LOCAL_ELIGIBLE_ROLES as readonly string[]).includes(role)
    && Array.isArray(context.availableLocalModels)
    && context.availableLocalModels.length > 0
  );
}

export function routeRoleProvider(
  settings: RoleRoutingSettings,
  role: string,
  context: RoleRoutingContext = {},
  routingTable: RoutingTable = DEFAULT_ROLE_ROUTING_TABLE,
): RoutedProviderConfig {
  const explicitProvider = roleProviderOverrideForSettings(settings, role);
  if (explicitProvider) {
    return configForExplicitProvider(settings, role, explicitProvider);
  }

  if (settings.roleRouting !== "auto") {
    return configForDefaultProvider(settings, role);
  }

  const preferences = routingTable[role] ?? ["mid_tier"];
  for (const preference of preferences) {
    if (preference === "local" && localRoutingIsAvailable(role, context)) {
      return configForProviderClass(settings, role, "local", context.availableLocalModels[0]);
    }
  }

  for (const preference of preferences) {
    if (preference === "frontier" || preference === "mid_tier" || preference === "fast") {
      return configForProviderClass(settings, role, preference);
    }
  }

  return configForProviderClass(settings, role, preferences[0] ?? "mid_tier");
}

export function estimateRoleRoutingCost(
  settings: RoleRoutingSettings,
  context: RoleRoutingContext = {},
): RoleRoutingCostEstimate {
  const roles = {} as RoleRoutingCostEstimate["roles"];
  let total = 0.0;
  let allFrontier = 0.0;

  for (const role of ROUTED_GENERATION_ROLES) {
    const routed = routeRoleProvider(settings, role, context);
    total += routed.estimatedCostPer1kTokens;
    allFrontier += providerClassCost("frontier");
    roles[role] = {
      providerClass: routed.providerClass,
      providerType: routed.providerType,
      costPer1kTokens: routed.estimatedCostPer1kTokens,
    };
  }

  return {
    totalPer1kTokens: total,
    allFrontierPer1kTokens: allFrontier,
    savingsVsAllFrontier: allFrontier - total,
    roles,
  };
}
