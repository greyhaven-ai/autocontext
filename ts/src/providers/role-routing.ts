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

export const CAPABILITY_RANK = _contract.CAPABILITY_RANK;

export const LOCAL_ARTIFACT_CAPABILITY = _contract.LOCAL_ARTIFACT_CAPABILITY;

// Conservative fallback for endpoints without explicit hosting. Unknown
// transports count as remote so an unrecognized name never reports zero cost.
export const PROVIDER_HOSTING = _contract.PROVIDER_HOSTING;

type ProviderHosting = "local" | "remote";

function parseDeclaredHosting(
  value: string | undefined,
  settingName: string,
): ProviderHosting | undefined {
  const declared = clean(value)?.toLowerCase();
  if (declared === undefined) return undefined;
  if (declared !== "local" && declared !== "remote") {
    throw new Error(`${settingName}=${JSON.stringify(value)} is invalid; expected local or remote`);
  }
  return declared;
}

function hostingFor(providerType: string, declaredHosting: string | undefined): ProviderHosting {
  const declared = parseDeclaredHosting(declaredHosting, "provider hosting");
  if (declared !== undefined) return declared;
  return PROVIDER_HOSTING[providerType.trim().toLowerCase()] === "local" ? "local" : "remote";
}

// The capability a role needs: its first API-backed preference.
function roleMinimumCapability(preferences: readonly ProviderClass[]): ProviderClass | undefined {
  return preferences.find((preference) => preference in CAPABILITY_RANK);
}

// Roles a local artifact may serve. Derived from the routing table and the
// artifact's declared capability rather than enumerated (AC-911): a role is
// eligible when it lists "local" as a preference and the artifact is at least as
// capable as the role requires. Declaring the artifact less capable therefore
// narrows this automatically, instead of requiring a second list to be edited in
// step with the first.
export const LOCAL_ELIGIBLE_ROLES: readonly string[] = Object.entries<readonly ProviderClass[]>(
  DEFAULT_ROLE_ROUTING_TABLE,
).flatMap(([role, preferences]) => {
  if (!preferences.includes("local")) return [];
  const minimum = roleMinimumCapability(preferences);
  if (minimum === undefined) return [];
  return CAPABILITY_RANK[LOCAL_ARTIFACT_CAPABILITY] >= CAPABILITY_RANK[minimum] ? [role] : [];
});

// Typed Record<string, ProviderClass> in the generated module itself (not
// Record<string, string>), so a contract value that isn't a declared provider class
// fails to compile there instead of surfacing later as a mistyped ProviderClass deep
// inside routing logic.
export const EXPLICIT_PROVIDER_CLASS = _contract.EXPLICIT_PROVIDER_CLASS;

export const PROVIDER_DEFAULT_MODEL = _contract.PROVIDER_DEFAULT_MODEL;

export const MODEL_DEFAULT_PRESERVED_PROVIDERS = _contract.MODEL_DEFAULT_PRESERVED_PROVIDERS;

// Python settings key -> the RoleRoutingSettings field holding the same value.
// Typed in the generated module against `keyof RoleRoutingSettings`, so a contract
// entry naming a field this package lacks fails to compile there. Exported because
// the cross-language replays translate one shared fixture into both spellings and
// must not hand-maintain a second copy of this mapping.
export const SETTINGS_KEY_MAP = _contract.SETTINGS_KEY_MAP;

const DEFAULT_ROLE_MODELS: Record<GenerationRole, string> = {
  competitor: "claude-sonnet-5",
  analyst: "claude-sonnet-5",
  coach: "claude-opus-5",
  architect: "claude-opus-5",
  curator: "claude-opus-5",
  translator: "claude-sonnet-5",
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
  /**
   * Capability class this endpoint declares: "frontier", "mid_tier", or "fast".
   * Applies only to locally hosted transports, where inferring capability from the
   * transport name is what AC-911 retires. Empty means fall back to inference.
   */
  providerCapability?: string;
  /** Hosting for the default endpoint. Empty falls back to transport inference. */
  providerHosting?: string;
  competitorProviderCapability?: string;
  analystProviderCapability?: string;
  coachProviderCapability?: string;
  architectProviderCapability?: string;
  competitorProviderHosting?: string;
  analystProviderHosting?: string;
  coachProviderHosting?: string;
  architectProviderHosting?: string;
  /**
   * Settings keys that came from a preset, project config, or the environment,
   * as opposed to a schema default. The TypeScript counterpart of pydantic's
   * `model_fields_set` (AC-911), populated by `buildSettingsAssemblyInput()`.
   *
   * Omitted entirely means "no information", and per-provider model resolution
   * then leaves every configured value alone. That is deliberate: a caller that
   * cannot say what the user chose must not have choices made on its behalf.
   */
  configuredFields?: readonly string[];
}

/**
 * The settings keys that hold a plain string value. `configuredFields` is
 * bookkeeping about the other keys rather than a setting, so excluding it lets
 * a shared fixture assign string values through SETTINGS_KEY_MAP without a cast.
 */
export type StringSettingKey = Exclude<keyof RoleRoutingSettings, "configuredFields">;

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

/**
 * @deprecated This dollar-based aggregate is not meaningful for self-hosted
 * runs. It remains available only for compatibility with the public API shipped
 * in 0.14.0. New code should inspect each `RoutedProviderConfig` instead.
 */
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

function roleSpecificCapability(role: string, settings: RoleRoutingSettings): string | undefined {
  switch (role) {
    case "competitor":
      return clean(settings.competitorProviderCapability);
    case "analyst":
      return clean(settings.analystProviderCapability);
    case "coach":
      return clean(settings.coachProviderCapability);
    case "architect":
      return clean(settings.architectProviderCapability);
    default:
      return undefined;
  }
}

function roleSpecificHosting(role: string, settings: RoleRoutingSettings): string | undefined {
  switch (role) {
    case "competitor":
      return clean(settings.competitorProviderHosting);
    case "analyst":
      return clean(settings.analystProviderHosting);
    case "coach":
      return clean(settings.coachProviderHosting);
    case "architect":
      return clean(settings.architectProviderHosting);
    default:
      return undefined;
  }
}

/**
 * The model to send when a role/tier slot was never configured (AC-912, ported
 * to TypeScript by AC-911).
 *
 * Python distinguishes "the user chose this" from "nobody touched it" via
 * pydantic's `model_fields_set`. TypeScript needs the same signal and cannot
 * infer it from the value: `AppSettingsSchema` has already substituted
 * "claude-opus-5" by the time routing runs, so an unset field and a
 * deliberate Claude choice look identical. `settings.configuredFields` carries
 * that distinction across, recorded by `buildSettingsAssemblyInput()` before
 * the schema defaults are applied.
 *
 * When `configuredFields` is absent the caller cannot say what was chosen, so
 * nothing is rewritten and behavior is exactly what it was before AC-911. An
 * explicit choice always wins, and providers in
 * MODEL_DEFAULT_PRESERVED_PROVIDERS are never rewritten, so Anthropic routing
 * is byte-identical either way.
 */
/**
 * The settings fields that hold a model id. Naming this set (rather than using
 * `keyof RoleRoutingSettings`) keeps `settings[field]` typed as `string |
 * undefined`, since `configuredFields` is the one non-string member.
 */
type ModelFieldKey =
  | "modelCompetitor"
  | "modelAnalyst"
  | "modelCoach"
  | "modelArchitect"
  | "modelCurator"
  | "modelTranslator"
  | "tierOpusModel"
  | "tierSonnetModel"
  | "tierHaikuModel";

function resolveModelDefault(
  settings: RoleRoutingSettings,
  providerType: string,
  fieldName: ModelFieldKey,
  configured: string | undefined,
  shippedDefault: string,
): string {
  const fallback = clean(configured) ?? shippedDefault;
  if (settings.configuredFields === undefined) return fallback;
  if (settings.configuredFields.includes(fieldName)) return fallback;

  const normalized = providerType.trim().toLowerCase();
  if ((MODEL_DEFAULT_PRESERVED_PROVIDERS as readonly string[]).includes(normalized)) {
    return fallback;
  }

  const localModel = clean(settings.localModel);
  if (localModel) return localModel;

  return PROVIDER_DEFAULT_MODEL[normalized] ?? fallback;
}

function roleSpecificModel(
  role: string,
  settings: RoleRoutingSettings,
  providerType: string,
): string {
  const resolve = (field: ModelFieldKey, shippedDefault: string): string =>
    resolveModelDefault(settings, providerType, field, settings[field], shippedDefault);
  switch (role) {
    case "competitor":
      return resolve("modelCompetitor", DEFAULT_ROLE_MODELS.competitor);
    case "analyst":
      return resolve("modelAnalyst", DEFAULT_ROLE_MODELS.analyst);
    case "coach":
      return resolve("modelCoach", DEFAULT_ROLE_MODELS.coach);
    case "architect":
      return resolve("modelArchitect", DEFAULT_ROLE_MODELS.architect);
    case "curator":
      return resolve("modelCurator", DEFAULT_ROLE_MODELS.curator);
    case "translator":
      return resolve("modelTranslator", DEFAULT_ROLE_MODELS.translator);
    default:
      return resolve("tierSonnetModel", "claude-sonnet-5");
  }
}

function tierModel(
  providerClass: ProviderClass,
  settings: RoleRoutingSettings,
  providerType: string,
): string {
  const resolve = (field: ModelFieldKey, shippedDefault: string): string =>
    resolveModelDefault(settings, providerType, field, settings[field], shippedDefault);
  switch (providerClass) {
    case "frontier":
      return resolve("tierOpusModel", "claude-opus-5");
    case "mid_tier":
    case "code_policy":
      return resolve("tierSonnetModel", "claude-sonnet-5");
    case "fast":
      return resolve("tierHaikuModel", "claude-haiku-4-5-20251001");
    case "local":
      // The artifact path has its own setting; it never falls back to a role or
      // tier default, so provider-default resolution does not apply.
      return clean(settings.mlxModelPath) ?? "local";
  }
}

/** Parse and validate a capability declaration from a routing-settings boundary. */
function parseDeclaredCapability(
  value: string | undefined,
  settingName: string,
): ProviderClass | undefined {
  const declared = clean(value)?.toLowerCase();
  if (declared === undefined) return undefined;
  if (!(declared in CAPABILITY_RANK)) {
    throw new Error(
      `${settingName}=${JSON.stringify(value)} is not a capability; expected fast, mid_tier, or frontier`,
    );
  }
  return declared as ProviderClass;
}

/**
 * Prefer an explicit capability for a locally hosted endpoint. Remote endpoints
 * retain their transport fallback, and "local" remains the artifact-slot class.
 */
function effectiveCapability(
  providerType: string,
  inferred: ProviderClass,
  declaredCapability: string | undefined,
  declaredHosting: string | undefined,
): ProviderClass {
  const declared = parseDeclaredCapability(declaredCapability, "provider capability");
  if (declared === undefined || inferred === "local") return inferred;
  if (hostingFor(providerType, declaredHosting) !== "local") return inferred;
  return declared;
}

/** Lower a requested capability to what the endpoint declares, never raise it. */
function clampToDeclared(
  settings: RoleRoutingSettings,
  providerType: string,
  requested: ProviderClass,
): ProviderClass {
  if (!(requested in CAPABILITY_RANK)) return requested;
  const declared = effectiveCapability(
    providerType,
    requested,
    settings.providerCapability,
    settings.providerHosting,
  );
  if (!(declared in CAPABILITY_RANK)) return requested;
  return CAPABILITY_RANK[declared] < CAPABILITY_RANK[requested] ? declared : requested;
}

/**
 * Cost is a function of hosting, not capability (AC-911). Self-hosted inference
 * has no per-token API cost, however capable the model behind it is. Keying this
 * on capability is what made a fully self-hosted run report the same $/1k as an
 * all-Anthropic one.
 */
function costFor(
  providerClass: ProviderClass,
  providerType: string,
  declaredHosting: string | undefined,
): number {
  if (hostingFor(providerType, declaredHosting) === "local") return 0;
  return PROVIDER_CLASS_COST_PER_1K_TOKENS[providerClass] ?? 0.003;
}

function executableInTypeScript(providerType: string): boolean {
  return (SUPPORTED_PROVIDER_TYPES as readonly string[]).includes(providerType);
}

function routedConfig(
  role: string,
  providerType: string,
  providerClass: ProviderClass,
  model: string,
  declaredHosting?: string,
): RoutedProviderConfig {
  const executable = executableInTypeScript(providerType);
  return {
    role,
    providerType,
    providerClass,
    model,
    estimatedCostPer1kTokens: costFor(providerClass, providerType, declaredHosting),
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
    const inferred = EXPLICIT_PROVIDER_CLASS[providerType] ?? "frontier";
    const declaredCapability = roleSpecificCapability(role, settings);
    const declaredHosting = roleSpecificHosting(role, settings);
    const providerClass = effectiveCapability(
      providerType,
      inferred,
      declaredCapability,
      declaredHosting,
    );
    const model =
      providerClass === "local"
        ? tierModel("local", settings, providerType)
        : roleSpecificModel(role, settings, providerType);
    return routedConfig(role, providerType, providerClass, model, declaredHosting);
  }

  const providerType = normalizeProvider(
    (context.preferProviderOverride ? clean(context.providerOverride) : undefined) ??
      envProviderOverride() ??
      clean(context.providerOverride) ??
      settings.agentProvider,
  );
  const inferred = EXPLICIT_PROVIDER_CLASS[providerType] ?? "mid_tier";
  const providerClass = effectiveCapability(
    providerType,
    inferred,
    settings.providerCapability,
    settings.providerHosting,
  );

  if (settings.roleRouting !== "auto") {
    const model =
      providerClass === "local"
        ? tierModel("local", settings, providerType)
        : roleSpecificModel(role, settings, providerType);
    return routedConfig(role, providerType, providerClass, model, settings.providerHosting);
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
    return routedConfig(role, "mlx", "local", localModel, "local");
  }

  // A role asks for a capability; an endpoint has one. Asking for frontier from
  // an endpoint declared mid_tier does not make it frontier, so the request is
  // clamped down to what the endpoint offers and both the tier model and the
  // reported class follow the clamped value.
  const effective = clampToDeclared(settings, providerType, preferences[0]);
  return routedConfig(
    role,
    providerType,
    effective,
    tierModel(effective, settings, providerType),
    settings.providerHosting,
  );
}

/**
 * @deprecated This dollar-based aggregate is not meaningful for self-hosted
 * runs. It remains available only for compatibility with the public API shipped
 * in 0.14.0. New code should inspect each result from `routeRoleProvider`.
 */
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
