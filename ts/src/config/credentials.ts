/**
 * Credential storage with hardened security (AC-430).
 *
 * Phase 1: Multi-provider store, 0600 perms, literal key enforcement, key validation
 * Phase 2: Known providers registry, expanded validation, selective removal, discovery
 */

export {
  CREDENTIALS_FILE,
  resolveApiKeyValue,
  saveProviderCredentials,
  loadProviderCredentials,
  listConfiguredProviders,
  removeProviderCredentials,
  type ProviderCredentials,
  type ProviderAuthStatus,
} from "./credential-store.js";

export {
  KNOWN_PROVIDERS,
  getKnownProvider,
  discoverAllProviders,
  type KnownProvider,
  type DiscoveredProvider,
} from "./credential-provider-discovery.js";

export {
  PROVIDER_MODELS,
  getModelsForProvider,
  resolveModel,
  listAuthenticatedModels,
  type KnownModel,
  type ResolveModelOpts,
  type AuthenticatedModel,
} from "./credential-model-catalog.js";

export {
  validateApiKey,
  type ValidationResult,
} from "./credential-validation.js";
