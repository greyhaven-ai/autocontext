import process from "node:process";

import { readCredentialStore, type ProviderAuthStatus } from "./credential-store.js";
import {
  getKnownProvider,
  KNOWN_PROVIDERS,
  type KnownProvider,
} from "./credential-provider-registry.js";

export {
  getKnownProvider,
  KNOWN_PROVIDERS,
  type KnownProvider,
} from "./credential-provider-registry.js";

export interface DiscoveredProvider extends ProviderAuthStatus {
  source: "stored" | "env";
}

function getGenericEnvProvider(): string | undefined {
  const provider = process.env.AUTOCONTEXT_AGENT_PROVIDER ?? process.env.AUTOCONTEXT_PROVIDER;
  const trimmed = provider?.trim().toLowerCase();
  return trimmed ? trimmed : undefined;
}

function getGenericEnvApiKey(): string | undefined {
  const apiKey = process.env.AUTOCONTEXT_AGENT_API_KEY ?? process.env.AUTOCONTEXT_API_KEY;
  return apiKey?.trim() ? apiKey : undefined;
}

function getGenericEnvModel(): string | undefined {
  const model = process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL ?? process.env.AUTOCONTEXT_MODEL;
  return model?.trim() ? model : undefined;
}

function getGenericEnvBaseUrl(): string | undefined {
  const baseUrl = process.env.AUTOCONTEXT_AGENT_BASE_URL ?? process.env.AUTOCONTEXT_BASE_URL;
  return baseUrl?.trim() ? baseUrl : undefined;
}

export function discoverAllProviders(configDir: string): DiscoveredProvider[] {
  const discovered: DiscoveredProvider[] = [];
  const seen = new Set<string>();

  const store = readCredentialStore(configDir);
  for (const [provider, credentials] of Object.entries(store.providers)) {
    seen.add(provider);
    discovered.push({
      provider,
      hasApiKey: Boolean(credentials.apiKey),
      source: "stored",
      ...(credentials.model ? { model: credentials.model } : {}),
      ...(credentials.baseUrl ? { baseUrl: credentials.baseUrl } : {}),
      ...(credentials.savedAt ? { savedAt: credentials.savedAt } : {}),
    });
  }

  const genericProvider = getGenericEnvProvider();
  if (genericProvider && !seen.has(genericProvider)) {
    const knownProvider = getKnownProvider(genericProvider);
    const providerSpecificKey = knownProvider?.envVar
      ? process.env[knownProvider.envVar]
      : undefined;
    discovered.push({
      provider: genericProvider,
      hasApiKey:
        Boolean(getGenericEnvApiKey() ?? providerSpecificKey) ||
        Boolean(knownProvider && !knownProvider.requiresKey),
      source: "env",
      ...(getGenericEnvModel() ? { model: getGenericEnvModel() } : {}),
      ...(getGenericEnvBaseUrl() ? { baseUrl: getGenericEnvBaseUrl() } : {}),
    });
    seen.add(genericProvider);
  }

  for (const knownProvider of KNOWN_PROVIDERS) {
    if (seen.has(knownProvider.id) || !knownProvider.envVar) {
      continue;
    }
    if (process.env[knownProvider.envVar]) {
      discovered.push({
        provider: knownProvider.id,
        hasApiKey: true,
        source: "env",
      });
    }
  }

  return discovered;
}
