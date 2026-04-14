export const LOGIN_HELP_TEXT = `autoctx login — Store provider credentials persistently

Usage: autoctx login [options]

Options:
  --provider <type>    Provider name: anthropic, openai, gemini, ollama, groq, etc.
  --key <api-key>      API key (omit to be prompted interactively)
  --model <name>       Default model for this provider
  --base-url <url>     Custom base URL (for Ollama, vLLM, proxies)
  --config-dir <path>  Config directory (default: ~/.config/autoctx)

Without flags, prompts interactively for provider and key.
Keys starting with ! are executed as shell commands (e.g. !security find-generic-password).

Examples:
  autoctx login --provider anthropic --key YOUR_ANTHROPIC_API_KEY
  autoctx login --provider ollama --base-url http://localhost:11434
  autoctx login                            # interactive prompt

See also: whoami, logout, providers, models`;

export const LOGOUT_HELP_TEXT = [
  "autoctx logout [--config-dir <path>]",
  "Clears stored provider credentials.",
].join("\n");

export interface LoginCommandValues {
  provider?: string;
  key?: string;
  model?: string;
  "base-url"?: string;
  "config-dir"?: string;
}

export interface ResolvedLoginCommand {
  provider: string;
  apiKey?: string;
  model?: string;
  baseUrl?: string;
  configDir?: string;
}

export interface ProviderSummary {
  provider: string;
  hasApiKey: boolean;
  source?: "stored" | "env";
  model?: string;
  baseUrl?: string;
  savedAt?: string;
}

export interface KnownProviderSummary {
  id: string;
  displayName: string;
  requiresKey: boolean;
}

export async function resolveLoginCommandRequest(
  values: LoginCommandValues,
  deps: {
    promptForValue(label: string): Promise<string>;
    normalizeOllamaBaseUrl(baseUrl?: string): string;
    validateOllamaConnection(baseUrl: string): Promise<void>;
    env: Record<string, string | undefined>;
  },
): Promise<ResolvedLoginCommand> {
  let provider = values.provider?.trim();
  if (!provider) {
    provider = await deps.promptForValue("Provider");
  }
  if (!provider) {
    throw new Error("Error: provider is required");
  }
  provider = provider.toLowerCase();

  let apiKey = values.key?.trim();
  let baseUrl = values["base-url"]?.trim();
  const model = values.model?.trim();

  if (provider === "ollama") {
    baseUrl = deps.normalizeOllamaBaseUrl(
      baseUrl ??
        deps.env.AUTOCONTEXT_AGENT_BASE_URL ??
        deps.env.AUTOCONTEXT_BASE_URL ??
        "http://localhost:11434",
    );
    await deps.validateOllamaConnection(baseUrl);
  } else {
    if (!apiKey) {
      apiKey = await deps.promptForValue("API key");
    }
    if (!apiKey) {
      throw new Error("Error: --key is required for this provider");
    }
  }

  return {
    provider,
    apiKey,
    model,
    baseUrl,
    configDir: values["config-dir"]?.trim() || undefined,
  };
}

export function buildStoredProviderCredentials(request: {
  apiKey?: string;
  model?: string;
  baseUrl?: string;
}): Record<string, string> {
  const creds: Record<string, string> = {};
  if (request.apiKey) creds.apiKey = request.apiKey;
  if (request.model) creds.model = request.model;
  if (request.baseUrl) creds.baseUrl = request.baseUrl;
  return creds;
}

export function buildLoginSuccessMessage(request: {
  provider: string;
  baseUrl?: string;
}): string {
  if (request.provider === "ollama") {
    return `Connected to Ollama at ${request.baseUrl}`;
  }
  return `Credentials saved for ${request.provider}`;
}

export function buildWhoamiPayload(input: {
  provider: string;
  model: string;
  authenticated: boolean;
  baseUrl?: string;
  configuredProviders: ProviderSummary[];
}): {
  provider: string;
  model: string;
  authenticated: boolean;
  baseUrl?: string;
  configuredProviders?: ProviderSummary[];
} {
  return {
    provider: input.provider,
    model: input.model,
    authenticated: input.authenticated,
    ...(input.baseUrl ? { baseUrl: input.baseUrl } : {}),
    ...(input.configuredProviders.length > 0
      ? { configuredProviders: input.configuredProviders }
      : {}),
  };
}

export function buildProvidersPayload(
  knownProviders: KnownProviderSummary[],
  discoveredProviders: ProviderSummary[],
): Array<{
  id: string;
  displayName: string;
  requiresKey: boolean;
  authenticated: boolean;
  source?: "stored" | "env";
  model?: string;
  baseUrl?: string;
}> {
  const discoveredMap = new Map(discoveredProviders.map((provider) => [provider.provider, provider]));
  return knownProviders.map((provider) => {
    const discovered = discoveredMap.get(provider.id);
    return {
      id: provider.id,
      displayName: provider.displayName,
      requiresKey: provider.requiresKey,
      authenticated: discovered
        ? discovered.hasApiKey || !provider.requiresKey
        : !provider.requiresKey,
      ...(discovered?.source ? { source: discovered.source } : {}),
      ...(discovered?.model ? { model: discovered.model } : {}),
      ...(discovered?.baseUrl ? { baseUrl: discovered.baseUrl } : {}),
    };
  });
}

export function renderModelsResult(models: unknown[]): string[] {
  if (models.length === 0) {
    return [
      JSON.stringify([]),
      "\nNo authenticated providers found. Run `autoctx login` to configure a provider.",
    ];
  }
  return [JSON.stringify(models, null, 2)];
}

export function buildLogoutMessage(existingProvider?: string): string {
  return existingProvider ? `Logged out from ${existingProvider}` : "Logged out.";
}
