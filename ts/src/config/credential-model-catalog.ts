import {
  discoverAllProviders,
  getKnownProvider,
} from "./credential-provider-discovery.js";
import { loadProviderCredentials } from "./credential-store.js";

export interface KnownModel {
  id: string;
  displayName: string;
}

export const PROVIDER_MODELS: Record<string, KnownModel[]> = {
  anthropic: [
    { id: "claude-sonnet-4-20250514", displayName: "Claude Sonnet 4" },
    { id: "claude-sonnet-4-5-20250929", displayName: "Claude Sonnet 4.5" },
    { id: "claude-opus-4-6", displayName: "Claude Opus 4.6" },
    { id: "claude-haiku-4-5-20251001", displayName: "Claude Haiku 4.5" },
  ],
  openai: [
    { id: "gpt-4o", displayName: "GPT-4o" },
    { id: "gpt-4o-mini", displayName: "GPT-4o Mini" },
    { id: "o3", displayName: "o3" },
    { id: "o4-mini", displayName: "o4 Mini" },
  ],
  gemini: [
    { id: "gemini-2.5-pro", displayName: "Gemini 2.5 Pro" },
    { id: "gemini-2.5-flash", displayName: "Gemini 2.5 Flash" },
    { id: "gemini-2.0-flash", displayName: "Gemini 2.0 Flash" },
  ],
  mistral: [
    { id: "mistral-large-latest", displayName: "Mistral Large" },
    { id: "mistral-medium-latest", displayName: "Mistral Medium" },
    { id: "codestral-latest", displayName: "Codestral" },
  ],
  groq: [
    { id: "llama-3.3-70b-versatile", displayName: "Llama 3.3 70B" },
    { id: "llama-3.1-8b-instant", displayName: "Llama 3.1 8B" },
    { id: "mixtral-8x7b-32768", displayName: "Mixtral 8x7B" },
  ],
  openrouter: [
    { id: "anthropic/claude-sonnet-4", displayName: "Claude Sonnet 4 (via OpenRouter)" },
    { id: "openai/gpt-4o", displayName: "GPT-4o (via OpenRouter)" },
    { id: "google/gemini-2.5-pro", displayName: "Gemini 2.5 Pro (via OpenRouter)" },
  ],
  "azure-openai": [
    { id: "gpt-4o", displayName: "GPT-4o (Azure)" },
    { id: "gpt-4o-mini", displayName: "GPT-4o Mini (Azure)" },
  ],
  minimax: [
    { id: "MiniMax-M3", displayName: "MiniMax M3" },
    { id: "MiniMax-M2.7", displayName: "MiniMax M2.7" },
  ],
};

export function getModelsForProvider(provider: string): KnownModel[] {
  return PROVIDER_MODELS[provider.toLowerCase()] ?? [];
}

export interface ResolveModelOpts {
  cliModel?: string;
  projectModel?: string;
  envModel?: string;
  configDir: string;
  provider: string;
}

export function resolveModel(opts: ResolveModelOpts): string | undefined {
  if (opts.cliModel) return opts.cliModel;
  if (opts.projectModel) return opts.projectModel;
  if (opts.envModel) return opts.envModel;

  const stored = loadProviderCredentials(opts.configDir, opts.provider);
  if (stored?.model) return stored.model;

  return getModelsForProvider(opts.provider)[0]?.id;
}

export interface AuthenticatedModel {
  provider: string;
  modelId: string;
  displayName: string;
}

export function listAuthenticatedModels(configDir: string): AuthenticatedModel[] {
  const discovered = discoverAllProviders(configDir);
  const authenticatedProviders = discovered.filter(
    (provider) => provider.hasApiKey || !getKnownProvider(provider.provider)?.requiresKey,
  );
  const models: AuthenticatedModel[] = [];

  for (const provider of authenticatedProviders) {
    for (const model of getModelsForProvider(provider.provider)) {
      models.push({
        provider: provider.provider,
        modelId: model.id,
        displayName: model.displayName,
      });
    }
  }

  return models;
}
