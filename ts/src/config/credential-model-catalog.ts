import { discoverAllProviders, getKnownProvider } from "./credential-provider-discovery.js";
import { loadProviderCredentials } from "./credential-store.js";

export interface KnownModel {
  id: string;
  displayName: string;
}

/**
 * Models offered in the picker, refreshed 2026-08-11.
 *
 * Provenance differs by vendor and that difference is load-bearing:
 *
 * * **anthropic** ids are confirmed against Anthropic's published ids.
 * * **openai** ids are confirmed against `GET https://api.openai.com/v1/models`
 *   on 2026-08-12. Worth knowing for the next refresh: that endpoint costs
 *   nothing and answers on a key with no credit balance, so a billing error
 *   from `chat/completions` is not a reason to leave ids unverified.
 * * **gemini / mistral** ids are derived from OpenRouter's live catalog by
 *   dropping the vendor prefix, and are the one group here still unverified --
 *   no key for either vendor was available. The mapping has always held
 *   (`openai/gpt-4o` served as `gpt-4o`, and it held for all three OpenAI ids
 *   above), but treat a 404 from one of these as a stale-id bug rather than a
 *   user misconfiguration.
 * * **openrouter** ids are used verbatim and are verified, since that is the
 *   catalog they came from.
 * * **groq** is deliberately untouched. Groq publishes its own ids with its
 *   own suffixes (`-versatile`, `-instant`) that do not exist upstream, so
 *   OpenRouter's listing cannot be translated into them by dropping a prefix
 *   the way the others can. Refreshing these needs Groq's own model list.
 */
export const PROVIDER_MODELS: Record<string, KnownModel[]> = {
  anthropic: [
    { id: "claude-opus-5", displayName: "Claude Opus 5" },
    { id: "claude-sonnet-5", displayName: "Claude Sonnet 5" },
    { id: "claude-fable-5", displayName: "Claude Fable 5" },
    { id: "claude-haiku-4-5-20251001", displayName: "Claude Haiku 4.5" },
  ],
  openai: [
    { id: "gpt-5.6-sol", displayName: "GPT-5.6 Sol (flagship)" },
    { id: "gpt-5.6-terra", displayName: "GPT-5.6 Terra (balanced)" },
    { id: "gpt-5.6-luna", displayName: "GPT-5.6 Luna (fast)" },
  ],
  gemini: [
    { id: "gemini-3.1-pro-preview", displayName: "Gemini 3.1 Pro Preview" },
    { id: "gemini-3.6-flash", displayName: "Gemini 3.6 Flash" },
    { id: "gemini-3.5-flash-lite", displayName: "Gemini 3.5 Flash Lite" },
  ],
  mistral: [
    { id: "mistral-large-2512", displayName: "Mistral Large" },
    { id: "mistral-medium-3-5", displayName: "Mistral Medium 3.5" },
    { id: "mistral-small-2603", displayName: "Mistral Small" },
    { id: "codestral-latest", displayName: "Codestral" },
  ],
  groq: [
    { id: "llama-3.3-70b-versatile", displayName: "Llama 3.3 70B" },
    { id: "llama-3.1-8b-instant", displayName: "Llama 3.1 8B" },
    { id: "mixtral-8x7b-32768", displayName: "Mixtral 8x7B" },
  ],
  openrouter: [
    { id: "anthropic/claude-opus-5", displayName: "Claude Opus 5 (via OpenRouter)" },
    { id: "anthropic/claude-sonnet-5", displayName: "Claude Sonnet 5 (via OpenRouter)" },
    // -pro is an OpenRouter serving variant (reasoning.mode=pro), not an
    // OpenAI model id, so it appears here and nowhere else in this file.
    // Confirmed rather than assumed: gpt-5.6-sol/terra/luna are all present on
    // the OpenAI models endpoint and gpt-5.6-sol-pro is not. (Earlier
    // generations did ship -pro as a real id -- gpt-5.5-pro, gpt-5.4-pro --
    // so this is specific to 5.6, not a rule about OpenAI naming.)
    { id: "openai/gpt-5.6-sol-pro", displayName: "GPT-5.6 Sol Pro (via OpenRouter)" },
    { id: "openai/gpt-5.6-terra", displayName: "GPT-5.6 Terra (via OpenRouter)" },
    { id: "google/gemini-3.1-pro-preview", displayName: "Gemini 3.1 Pro Preview (via OpenRouter)" },
  ],
  "azure-openai": [
    { id: "gpt-5.6-terra", displayName: "GPT-5.6 Terra (Azure)" },
    { id: "gpt-5.6-luna", displayName: "GPT-5.6 Luna (Azure)" },
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
