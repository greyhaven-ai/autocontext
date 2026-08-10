import { SUPPORTED_PROVIDER_TYPES } from "./supported-provider-types.js";
import * as _contract from "./role-routing-contract.generated.js";

// The values below derive from docs/role-routing-contract.json via the generated
// module role-routing-contract.generated.ts. To change a value, edit the contract
// and regenerate — do not edit here.

export const PROVIDER_CLASSES = _contract.PROVIDER_CLASSES;

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

export const ROLE_ROUTING_MODES = _contract.ROLE_ROUTING_MODES;

export type RoleRoutingMode = (typeof ROLE_ROUTING_MODES)[number];

export const PROVIDER_CLASS_COST_PER_1K_TOKENS: Partial<Record<ProviderClass, number>> =
  _contract.PROVIDER_CLASS_COST_PER_1K_TOKENS;

export const DEFAULT_ROLE_ROUTING_TABLE = _contract.DEFAULT_ROLE_ROUTING_TABLE satisfies Record<
  GenerationRole,
  readonly ProviderClass[]
>;

export const LOCAL_ELIGIBLE_ROLES =
  _contract.LOCAL_ELIGIBLE_ROLES satisfies readonly GenerationRole[];

// Typed Record<string, ProviderClass> in the generated module itself (not
// Record<string, string>), so a contract value that isn't a declared provider class
// fails to compile there instead of surfacing later as a mistyped ProviderClass deep
// inside routing logic.
export const EXPLICIT_PROVIDER_CLASS = _contract.EXPLICIT_PROVIDER_CLASS;

// Python settings key -> the RoleRoutingSettings field holding the same value.
// Typed in the generated module against `keyof RoleRoutingSettings`, so a contract
// entry naming a field this package lacks fails to compile there. Exported because
// the cross-language replays translate one shared fixture into both spellings and
// must not hand-maintain a second copy of this mapping.
export const SETTINGS_KEY_MAP = _contract.SETTINGS_KEY_MAP;

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
  /**
   * Single model id filling every role/tier slot the user has not configured, for
   * providers that are not Anthropic. Python's counterpart is `local_model`
   * (AC-912). Declared here so the shared contract's SETTINGS_KEY_MAP can name it
   * and neither package can carry a routing setting the other has never heard of.
   */
  localModel?: string;
}

export interface RoleRoutingContext {
  availableLocalModels?: readonly string[];
  /**
   * Explicit provider override (e.g. the CLI `--provider` flag, `RunManager({ providerType })`,
   * or a `switch_provider` command). Takes precedence over `settings.agentProvider` for any
   * role that doesn't already have its own role-specific provider setting (which stays the
   * highest-priority route). When omitted, routing falls back to the env-derived
   * `settings.agentProvider`, unchanged from prior behavior.
   *
   * By default a live `AUTOCONTEXT_AGENT_PROVIDER`/`AUTOCONTEXT_PROVIDER` env var still
   * outranks this override (construction-time overrides, e.g. the CLI `--provider` flag,
   * keep that precedent). Set `preferProviderOverride` to flip that for a deliberate
   * mid-session switch (e.g. `switch_provider`), which should win regardless of a pinned env
   * var.
   */
  providerOverride?: string;
  /**
   * When true, `providerOverride` wins over a live env var instead of losing to it. Mirrors
   * `resolveProviderConfig()`'s `preferProviderOverride` option; intended for overrides that
   * represent an explicit runtime decision (a session's active provider was just switched)
   * rather than a process-startup default.
   */
  preferProviderOverride?: boolean;
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

// Boundary: reads live process.env rather than the settings snapshot (settings.agentProvider
// was captured once by loadSettings() and can be stale for a long-lived process). This is a
// deliberate divergence, not an oversight — it mirrors provider-config-resolution.ts's
// envProviderType read so the default provider (via resolveProviderConfig) and per-role
// routes (via routeRoleProvider) stay precedence-consistent: a live
// AUTOCONTEXT_AGENT_PROVIDER/AUTOCONTEXT_PROVIDER outranks an explicit providerOverride
// unless the caller sets `preferProviderOverride` (see RoleRoutingContext).
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
    (context.preferProviderOverride ? clean(context.providerOverride) : undefined) ??
      envProviderOverride() ??
      clean(context.providerOverride) ??
      settings.agentProvider,
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
