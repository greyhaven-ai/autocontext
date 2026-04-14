import { ProviderError } from "../types/index.js";
import { getKnownProvider, loadPersistedCredentials, loadProjectConfig } from "../config/index.js";
import { OPENAI_COMPATIBLE_PROVIDER_DEFAULTS } from "./provider-factory.js";

export interface ProviderConfig {
  providerType: string;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
}

export interface ResolveProviderConfigOpts {
  preferProviderOverride?: boolean;
  preferModelOverride?: boolean;
  preferApiKeyOverride?: boolean;
  preferBaseUrlOverride?: boolean;
}

export function resolveProviderConfig(
  overrides: Partial<ProviderConfig> = {},
  opts: ResolveProviderConfigOpts = {},
): ProviderConfig {
  const projectConfig = loadProjectConfig();
  const defaultPersistedCredentials = loadPersistedCredentials();
  const envProviderType =
    process.env.AUTOCONTEXT_AGENT_PROVIDER ??
    process.env.AUTOCONTEXT_PROVIDER;
  const envModel =
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL ??
    process.env.AUTOCONTEXT_MODEL;
  const envBaseUrl =
    process.env.AUTOCONTEXT_AGENT_BASE_URL ??
    process.env.AUTOCONTEXT_BASE_URL;
  const envGenericKey =
    process.env.AUTOCONTEXT_AGENT_API_KEY ??
    process.env.AUTOCONTEXT_API_KEY;

  const providerType =
    (opts.preferProviderOverride ? overrides.providerType : undefined) ??
    envProviderType ??
    overrides.providerType ??
    projectConfig?.provider ??
    defaultPersistedCredentials?.provider ??
    "anthropic";
  const persistedCredentials = loadPersistedCredentials(undefined, providerType);
  const model =
    (opts.preferModelOverride ? overrides.model : undefined) ??
    envModel ??
    overrides.model ??
    projectConfig?.model ??
    persistedCredentials?.model;
  const baseUrl =
    (opts.preferBaseUrlOverride ? overrides.baseUrl : undefined) ??
    envBaseUrl ??
    overrides.baseUrl ??
    persistedCredentials?.baseUrl;
  const genericKey =
    (opts.preferApiKeyOverride ? overrides.apiKey : undefined) ??
    envGenericKey ??
    overrides.apiKey ??
    persistedCredentials?.apiKey;

  const type = providerType.toLowerCase().trim();

  if (type === "deterministic") {
    return { providerType: type, model };
  }

  if (type === "anthropic") {
    const apiKey =
      genericKey ??
      process.env.ANTHROPIC_API_KEY ??
      process.env.AUTOCONTEXT_ANTHROPIC_API_KEY;
    if (!apiKey) {
      throw new ProviderError(
        "ANTHROPIC_API_KEY environment variable required (or set AUTOCONTEXT_API_KEY / AUTOCONTEXT_AGENT_API_KEY)",
      );
    }
    return { providerType: type, apiKey, model, baseUrl };
  }

  if (type === "ollama") {
    return {
      providerType: type,
      apiKey: genericKey ?? "ollama",
      baseUrl: baseUrl ?? "http://localhost:11434/v1",
      model: model ?? "llama3.1",
    };
  }

  if (type === "vllm") {
    return {
      providerType: type,
      apiKey: genericKey ?? "no-key",
      baseUrl: baseUrl ?? "http://localhost:8000/v1",
      model: model ?? "default",
    };
  }

  if (type === "hermes") {
    return {
      providerType: type,
      apiKey: genericKey ?? "no-key",
      baseUrl: baseUrl ?? "http://localhost:8080/v1",
      model: model ?? "hermes-3-llama-3.1-8b",
    };
  }

  if (type === "pi" || type === "pi-rpc") {
    return { providerType: type, apiKey: genericKey, baseUrl, model };
  }

  const providerSpecificEnvVar =
    OPENAI_COMPATIBLE_PROVIDER_DEFAULTS[type]?.envVar ??
    getKnownProvider(type)?.envVar;
  const providerSpecificKey = providerSpecificEnvVar
    ? process.env[providerSpecificEnvVar]
    : undefined;
  const openaiFallbackKey =
    type === "openai" || type === "openai-compatible"
      ? process.env.OPENAI_API_KEY
      : undefined;
  const apiKey = genericKey ?? providerSpecificKey ?? openaiFallbackKey;
  if (!apiKey) {
    const keyVars = [
      "AUTOCONTEXT_API_KEY",
      "AUTOCONTEXT_AGENT_API_KEY",
    ];
    if (providerSpecificEnvVar) {
      keyVars.push(providerSpecificEnvVar);
    } else if (type === "openai" || type === "openai-compatible") {
      keyVars.push("OPENAI_API_KEY");
    }
    throw new ProviderError(`API key required: set ${keyVars.join(", or ")}`);
  }

  return { providerType: type, apiKey, baseUrl, model };
}
