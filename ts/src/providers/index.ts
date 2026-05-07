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
  closeProviderBundle,
  createConfiguredProvider,
  withRuntimeSettings,
  type GenerationRole,
  type ProviderCompositionOpts,
  type ProviderRuntimeSessionOpts,
  type RoleProviderBundle,
  type RoleProviderSettings,
} from "./role-provider-bundle.js";
