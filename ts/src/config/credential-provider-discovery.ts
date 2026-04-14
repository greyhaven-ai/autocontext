import process from "node:process";

import { readCredentialStore, type ProviderAuthStatus } from "./credential-store.js";

export interface KnownProvider {
  id: string;
  displayName: string;
  keyPrefix?: string;
  defaultBaseUrl?: string;
  envVar?: string;
  requiresKey: boolean;
}

export const KNOWN_PROVIDERS: KnownProvider[] = [
  {
    id: "anthropic",
    displayName: "Anthropic",
    keyPrefix: "sk-ant-",
    envVar: "ANTHROPIC_API_KEY",
    requiresKey: true,
  },
  {
    id: "openai",
    displayName: "OpenAI",
    keyPrefix: "sk-",
    envVar: "OPENAI_API_KEY",
    requiresKey: true,
  },
  {
    id: "gemini",
    displayName: "Google Gemini",
    keyPrefix: "AIza",
    envVar: "GEMINI_API_KEY",
    requiresKey: true,
  },
  { id: "mistral", displayName: "Mistral", envVar: "MISTRAL_API_KEY", requiresKey: true },
  { id: "groq", displayName: "Groq", keyPrefix: "gsk_", envVar: "GROQ_API_KEY", requiresKey: true },
  {
    id: "openrouter",
    displayName: "OpenRouter",
    keyPrefix: "sk-or-",
    envVar: "OPENROUTER_API_KEY",
    requiresKey: true,
  },
  {
    id: "azure-openai",
    displayName: "Azure OpenAI",
    envVar: "AZURE_OPENAI_API_KEY",
    requiresKey: true,
  },
  {
    id: "ollama",
    displayName: "Ollama",
    defaultBaseUrl: "http://localhost:11434",
    requiresKey: false,
  },
  { id: "vllm", displayName: "vLLM", defaultBaseUrl: "http://localhost:8000", requiresKey: false },
  {
    id: "hermes",
    displayName: "Hermes Gateway",
    defaultBaseUrl: "http://localhost:8080",
    requiresKey: false,
  },
  { id: "openai-compatible", displayName: "OpenAI-Compatible", requiresKey: true },
  { id: "claude-cli", displayName: "Claude CLI", requiresKey: false },
  { id: "codex", displayName: "Codex CLI", requiresKey: false },
  { id: "pi", displayName: "Pi (CLI)", requiresKey: false },
  { id: "pi-rpc", displayName: "Pi (RPC)", requiresKey: false },
  { id: "deterministic", displayName: "Deterministic (testing)", requiresKey: false },
];

const KNOWN_PROVIDER_MAP = new Map(KNOWN_PROVIDERS.map((provider) => [provider.id, provider]));

export function getKnownProvider(id: string): KnownProvider | null {
  return KNOWN_PROVIDER_MAP.get(id.toLowerCase()) ?? null;
}

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
