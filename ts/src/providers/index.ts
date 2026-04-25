/**
 * Provider module facade — pluggable LLM provider construction and resolution.
 */

export {
  OPENAI_COMPATIBLE_PROVIDER_DEFAULTS,
  SUPPORTED_PROVIDER_TYPES,
  createAnthropicProvider,
  createOpenAICompatibleProvider,
  createProvider,
  type AnthropicProviderOpts,
  type OpenAICompatibleProviderOpts,
  type CreateProviderOpts,
} from "./provider-factory.js";

export {
  resolveProviderConfig,
  type ProviderConfig,
  type ResolveProviderConfigOpts,
} from "./provider-config-resolution.js";

export {
  buildRoleProviderBundle,
  createConfiguredProvider,
  withRuntimeSettings,
  type BuildRoleProviderBundleOptions,
  type RoleProviderBundle,
  type RoleProviderSettings,
} from "./role-provider-bundle.js";

export {
  DEFAULT_ROLE_ROUTING_TABLE,
  EXPLICIT_PROVIDER_CLASS,
  LOCAL_ELIGIBLE_ROLES,
  PROVIDER_CLASSES,
  PROVIDER_CLASS_COST_PER_1K_TOKENS,
  ROLE_ROUTING_MODES,
  ROUTED_GENERATION_ROLES,
  estimateRoleRoutingCost,
  routeRoleProvider,
  type GenerationRole,
  type ProviderClass,
  type RoleRoutingContext,
  type RoleRoutingCostEstimate,
  type RoleRoutingMode,
  type RoleRoutingSettings,
  type RoutedProviderConfig,
} from "./role-routing.js";
