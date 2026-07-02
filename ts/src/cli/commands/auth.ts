/**
 * `login` / `whoami` / `logout` / `providers` / `models` commands
 * (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { join } from "node:path";
import { errorMessage } from "./shared.js";

async function promptForValue(label: string): Promise<string> {
  const { createInterface } = await import("node:readline/promises");
  const rl = createInterface({ input: process.stdin, output: process.stderr });
  try {
    return (await rl.question(`${label}: `)).trim();
  } finally {
    rl.close();
  }
}

function normalizeOllamaBaseUrl(baseUrl?: string): string {
  const normalized = (baseUrl ?? "http://localhost:11434").replace(/\/+$/, "");
  return normalized.endsWith("/v1") ? normalized.slice(0, -3) : normalized;
}

async function validateOllamaConnection(baseUrl: string): Promise<void> {
  try {
    const response = await fetch(`${normalizeOllamaBaseUrl(baseUrl)}/api/tags`);
    if (!response.ok) {
      throw new Error(`Ollama connection failed: ${response.status} ${response.statusText}`);
    }
  } catch (err) {
    if (err instanceof Error && err.message.startsWith("Ollama connection failed:")) {
      throw err;
    }
    throw new Error(
      `Ollama connection failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

export async function cmdLogin(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      provider: { type: "string" },
      key: { type: "string" },
      model: { type: "string" },
      "base-url": { type: "string" },
      "config-dir": { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    buildLoginSuccessMessage,
    buildStoredProviderCredentials,
    LOGIN_HELP_TEXT,
    resolveLoginCommandRequest,
  } = await import("../auth-provider-command-workflow.js");

  if (values.help) {
    console.log(LOGIN_HELP_TEXT);
    process.exit(0);
  }

  const { resolveConfigDir } = await import("../../config/index.js");
  let request;
  try {
    request = await resolveLoginCommandRequest(values, {
      promptForValue,
      normalizeOllamaBaseUrl,
      validateOllamaConnection,
      env: process.env,
    });
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  // Validate API key format before saving (AC-430)
  if (request.apiKey) {
    const { validateApiKey, resolveApiKeyValue } = await import("../../config/credentials.js");
    // Resolve shell-command escape hatch (e.g. "!security find-generic-password -ws 'anthropic'")
    const resolvedKey = resolveApiKeyValue(request.apiKey);
    const validation = await validateApiKey(request.provider, resolvedKey);
    if (!validation.valid) {
      console.error(`Warning: ${validation.error}`);
    }
  }

  // Save to multi-provider credential store with 0600 permissions (AC-430)
  const { saveProviderCredentials } = await import("../../config/credentials.js");
  const configDir = resolveConfigDir(request.configDir);
  saveProviderCredentials(configDir, request.provider, buildStoredProviderCredentials(request));

  console.log(buildLoginSuccessMessage(request));
}

export async function cmdWhoami(): Promise<void> {
  const { buildWhoamiPayload } = await import("../auth-provider-command-workflow.js");
  const { loadPersistedCredentials, loadProjectConfig } = await import("../../config/index.js");
  const { resolveProviderConfig } = await import("../../providers/index.js");
  const { resolveConfigDir } = await import("../../config/index.js");

  const projectConfig = loadProjectConfig();
  const configDir = resolveConfigDir();
  const defaultPersistedCredentials = loadPersistedCredentials(configDir);
  let resolvedConfig: {
    providerType: string;
    apiKey?: string;
    model?: string;
    baseUrl?: string;
  } | null = null;

  try {
    resolvedConfig = resolveProviderConfig();
  } catch {
    resolvedConfig = null;
  }

  const provider =
    resolvedConfig?.providerType ??
    projectConfig?.provider ??
    defaultPersistedCredentials?.provider ??
    "not configured";
  const persistedCredentials =
    provider !== "not configured"
      ? loadPersistedCredentials(configDir, provider)
      : defaultPersistedCredentials;
  const model =
    resolvedConfig?.model ??
    projectConfig?.model ??
    persistedCredentials?.model ??
    process.env.AUTOCONTEXT_MODEL ??
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL ??
    "default";
  const baseUrl =
    resolvedConfig?.baseUrl ??
    persistedCredentials?.baseUrl ??
    process.env.AUTOCONTEXT_AGENT_BASE_URL ??
    process.env.AUTOCONTEXT_BASE_URL;
  const authenticated =
    provider === "ollama" ||
    Boolean(
      resolvedConfig?.apiKey ??
      process.env.ANTHROPIC_API_KEY ??
      process.env.OPENAI_API_KEY ??
      persistedCredentials?.apiKey,
    );

  // Also list all configured providers (AC-430)
  const { listConfiguredProviders } = await import("../../config/credentials.js");
  const configuredProviders = listConfiguredProviders(configDir);

  console.log(
    JSON.stringify(
      buildWhoamiPayload({
        provider,
        model,
        authenticated,
        baseUrl,
        configuredProviders,
      }),
      null,
      2,
    ),
  );
}

export async function cmdLogout(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      "config-dir": { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  });

  const { buildLogoutMessage, LOGOUT_HELP_TEXT } =
    await import("../auth-provider-command-workflow.js");

  if (values.help) {
    console.log(LOGOUT_HELP_TEXT);
    process.exit(0);
  }

  const { existsSync, unlinkSync } = await import("node:fs");
  const { loadPersistedCredentials, resolveConfigDir } = await import("../../config/index.js");
  const configDir = resolveConfigDir(values["config-dir"]);
  const credentialsPath = join(configDir, "credentials.json");
  const existing = loadPersistedCredentials(configDir);

  if (!existsSync(credentialsPath)) {
    console.log("No stored credentials found.");
    return;
  }

  unlinkSync(credentialsPath);
  console.log(buildLogoutMessage(existing?.provider));
}

export async function cmdProviders(): Promise<void> {
  const { buildProvidersPayload } = await import("../auth-provider-command-workflow.js");
  const { KNOWN_PROVIDERS, discoverAllProviders } = await import("../../config/credentials.js");
  const { resolveConfigDir } = await import("../../config/index.js");
  const configDir = resolveConfigDir();
  const discovered = discoverAllProviders(configDir);

  console.log(JSON.stringify(buildProvidersPayload(KNOWN_PROVIDERS, discovered), null, 2));
}

export async function cmdModels(): Promise<void> {
  const { renderModelsResult } = await import("../auth-provider-command-workflow.js");
  const { listAuthenticatedModels } = await import("../../config/credentials.js");
  const { resolveConfigDir } = await import("../../config/index.js");
  const configDir = resolveConfigDir();
  const models = listAuthenticatedModels(configDir);

  for (const line of renderModelsResult(models)) {
    console.log(line);
  }
}
