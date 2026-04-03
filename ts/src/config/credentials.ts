/**
 * Credential storage with hardened security (AC-430).
 *
 * Phase 1: Multi-provider store, 0600 perms, shell escape hatch, key validation
 * Phase 2: Known providers registry, expanded validation, selective removal, discovery
 */

import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

export const CREDENTIALS_FILE = "credentials.json";

export interface ProviderCredentials {
  apiKey?: string;
  model?: string;
  baseUrl?: string;
  savedAt?: string;
}

export interface ProviderAuthStatus {
  provider: string;
  hasApiKey: boolean;
  model?: string;
  baseUrl?: string;
  savedAt?: string;
}

export interface ValidationResult {
  valid: boolean;
  error?: string;
}

// ---------------------------------------------------------------------------
// Shell-command escape hatch
// ---------------------------------------------------------------------------

/**
 * Resolve an API key value. If the value starts with `!`, execute it as a
 * shell command and return the trimmed stdout. Otherwise return as-is.
 *
 * The `!` prefix is intentional: it allows users to store keychain lookups
 * like `!security find-generic-password -ws 'anthropic'` or
 * `!op read op://vault/anthropic/key` in their credentials file, matching
 * Pi's credential resolution pattern.
 *
 * This is NOT arbitrary user input — it's a value the user explicitly wrote
 * into their own config file, similar to Git's credential helpers.
 */
export function resolveApiKeyValue(value: string): string {
  if (!value || !value.startsWith("!")) {
    return value;
  }

  const command = value.slice(1).trim();
  // Use sh -c to support pipes, shell builtins, and complex commands
  // that keychain tools require (e.g. `security find-generic-password -ws 'name'`).
  // This is safe because the command comes from the user's own config file,
  // not from untrusted input.
  const result = execFileSync("/bin/sh", ["-c", command], {
    encoding: "utf-8",
    timeout: 10_000,
    stdio: ["pipe", "pipe", "pipe"],
  });
  return result.trim();
}

// ---------------------------------------------------------------------------
// Multi-provider credential store
// ---------------------------------------------------------------------------

interface CredentialStore {
  providers: Record<string, ProviderCredentials>;
}

function isLegacyFormat(data: Record<string, unknown>): boolean {
  return typeof data.provider === "string" && !data.providers;
}

function readStore(configDir: string): CredentialStore {
  const filePath = join(configDir, CREDENTIALS_FILE);
  if (!existsSync(filePath)) {
    return { providers: {} };
  }

  const raw = JSON.parse(readFileSync(filePath, "utf-8")) as Record<string, unknown>;

  // Legacy single-provider format migration
  if (isLegacyFormat(raw)) {
    const provider = (raw.provider as string).trim();
    const creds: ProviderCredentials = {};
    if (typeof raw.apiKey === "string" && raw.apiKey.trim()) creds.apiKey = raw.apiKey.trim();
    if (typeof raw.model === "string" && raw.model.trim()) creds.model = raw.model.trim();
    if (typeof raw.baseUrl === "string" && raw.baseUrl.trim()) creds.baseUrl = raw.baseUrl.trim();
    if (typeof raw.savedAt === "string" && raw.savedAt.trim()) creds.savedAt = raw.savedAt.trim();
    return { providers: { [provider]: creds } };
  }

  // New multi-provider format
  const providers: Record<string, ProviderCredentials> = {};
  const rawProviders = (raw.providers ?? {}) as Record<string, Record<string, unknown>>;
  for (const [name, entry] of Object.entries(rawProviders)) {
    const creds: ProviderCredentials = {};
    if (typeof entry.apiKey === "string") creds.apiKey = entry.apiKey;
    if (typeof entry.model === "string") creds.model = entry.model;
    if (typeof entry.baseUrl === "string") creds.baseUrl = entry.baseUrl;
    if (typeof entry.savedAt === "string") creds.savedAt = entry.savedAt;
    providers[name] = creds;
  }
  return { providers };
}

function writeStore(configDir: string, store: CredentialStore): void {
  mkdirSync(configDir, { recursive: true });
  const filePath = join(configDir, CREDENTIALS_FILE);
  writeFileSync(filePath, JSON.stringify(store, null, 2), "utf-8");
  chmodSync(filePath, 0o600);
}

export function saveProviderCredentials(
  configDir: string,
  provider: string,
  creds: Omit<ProviderCredentials, "savedAt">,
): void {
  const store = readStore(configDir);
  store.providers[provider] = {
    ...creds,
    savedAt: new Date().toISOString(),
  };
  writeStore(configDir, store);
}

export function loadProviderCredentials(
  configDir: string,
  provider: string,
): ProviderCredentials | null {
  const store = readStore(configDir);
  return store.providers[provider] ?? null;
}

export function listConfiguredProviders(configDir: string): ProviderAuthStatus[] {
  const store = readStore(configDir);
  return Object.entries(store.providers).map(([name, creds]) => ({
    provider: name,
    hasApiKey: Boolean(creds.apiKey),
    ...(creds.model ? { model: creds.model } : {}),
    ...(creds.baseUrl ? { baseUrl: creds.baseUrl } : {}),
    ...(creds.savedAt ? { savedAt: creds.savedAt } : {}),
  }));
}

// ---------------------------------------------------------------------------
// API key format validation
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Known providers registry (AC-430 Phase 2)
// ---------------------------------------------------------------------------

export interface KnownProvider {
  id: string;
  displayName: string;
  keyPrefix?: string;
  defaultBaseUrl?: string;
  envVar?: string;
  requiresKey: boolean;
}

export const KNOWN_PROVIDERS: KnownProvider[] = [
  { id: "anthropic", displayName: "Anthropic", keyPrefix: "sk-ant-", envVar: "ANTHROPIC_API_KEY", requiresKey: true },
  { id: "openai", displayName: "OpenAI", keyPrefix: "sk-", envVar: "OPENAI_API_KEY", requiresKey: true },
  { id: "gemini", displayName: "Google Gemini", keyPrefix: "AIza", envVar: "GEMINI_API_KEY", requiresKey: true },
  { id: "mistral", displayName: "Mistral", envVar: "MISTRAL_API_KEY", requiresKey: true },
  { id: "groq", displayName: "Groq", keyPrefix: "gsk_", envVar: "GROQ_API_KEY", requiresKey: true },
  { id: "openrouter", displayName: "OpenRouter", keyPrefix: "sk-or-", envVar: "OPENROUTER_API_KEY", requiresKey: true },
  { id: "azure-openai", displayName: "Azure OpenAI", envVar: "AZURE_OPENAI_API_KEY", requiresKey: true },
  { id: "ollama", displayName: "Ollama", defaultBaseUrl: "http://localhost:11434", requiresKey: false },
  { id: "vllm", displayName: "vLLM", defaultBaseUrl: "http://localhost:8000", requiresKey: false },
  { id: "hermes", displayName: "Hermes Gateway", defaultBaseUrl: "http://localhost:8080", requiresKey: false },
  { id: "openai-compatible", displayName: "OpenAI-Compatible", requiresKey: true },
  { id: "pi", displayName: "Pi (CLI)", requiresKey: false },
  { id: "pi-rpc", displayName: "Pi (RPC)", requiresKey: false },
  { id: "deterministic", displayName: "Deterministic (testing)", requiresKey: false },
];

const KNOWN_PROVIDER_MAP = new Map(KNOWN_PROVIDERS.map((p) => [p.id, p]));

export function getKnownProvider(id: string): KnownProvider | null {
  return KNOWN_PROVIDER_MAP.get(id.toLowerCase()) ?? null;
}

// ---------------------------------------------------------------------------
// Selective provider removal
// ---------------------------------------------------------------------------

export function removeProviderCredentials(configDir: string, provider: string): boolean {
  const store = readStore(configDir);
  if (!(provider in store.providers)) {
    return false;
  }
  delete store.providers[provider];
  writeStore(configDir, store);
  return true;
}

// ---------------------------------------------------------------------------
// Provider discovery — merge stored + env var credentials
// ---------------------------------------------------------------------------

export interface DiscoveredProvider extends ProviderAuthStatus {
  source: "stored" | "env";
}

function getGenericEnvProvider(): string | undefined {
  const provider =
    process.env.AUTOCONTEXT_AGENT_PROVIDER ??
    process.env.AUTOCONTEXT_PROVIDER;
  const trimmed = provider?.trim().toLowerCase();
  return trimmed ? trimmed : undefined;
}

function getGenericEnvApiKey(): string | undefined {
  const apiKey =
    process.env.AUTOCONTEXT_AGENT_API_KEY ??
    process.env.AUTOCONTEXT_API_KEY;
  return apiKey?.trim() ? apiKey : undefined;
}

function getGenericEnvModel(): string | undefined {
  const model =
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL ??
    process.env.AUTOCONTEXT_MODEL;
  return model?.trim() ? model : undefined;
}

function getGenericEnvBaseUrl(): string | undefined {
  const baseUrl =
    process.env.AUTOCONTEXT_AGENT_BASE_URL ??
    process.env.AUTOCONTEXT_BASE_URL;
  return baseUrl?.trim() ? baseUrl : undefined;
}

export function discoverAllProviders(configDir: string): DiscoveredProvider[] {
  const result: DiscoveredProvider[] = [];
  const seen = new Set<string>();

  // Stored credentials first (higher precedence)
  const store = readStore(configDir);
  for (const [name, creds] of Object.entries(store.providers)) {
    seen.add(name);
    result.push({
      provider: name,
      hasApiKey: Boolean(creds.apiKey),
      source: "stored",
      ...(creds.model ? { model: creds.model } : {}),
      ...(creds.baseUrl ? { baseUrl: creds.baseUrl } : {}),
      ...(creds.savedAt ? { savedAt: creds.savedAt } : {}),
    });
  }

  const genericProvider = getGenericEnvProvider();
  if (genericProvider && !seen.has(genericProvider)) {
    const known = getKnownProvider(genericProvider);
    const providerSpecificKey = known?.envVar ? process.env[known.envVar] : undefined;
    result.push({
      provider: genericProvider,
      hasApiKey: Boolean(getGenericEnvApiKey() ?? providerSpecificKey) || Boolean(known && !known.requiresKey),
      source: "env",
      ...(getGenericEnvModel() ? { model: getGenericEnvModel() } : {}),
      ...(getGenericEnvBaseUrl() ? { baseUrl: getGenericEnvBaseUrl() } : {}),
    });
    seen.add(genericProvider);
  }

  // Then check env vars for known providers not already stored
  for (const known of KNOWN_PROVIDERS) {
    if (seen.has(known.id)) continue;
    if (!known.envVar) continue;
    const envValue = process.env[known.envVar];
    if (envValue) {
      result.push({
        provider: known.id,
        hasApiKey: true,
        source: "env",
      });
    }
  }

  return result;
}

// ---------------------------------------------------------------------------
// Known models per provider (AC-430 Phase 3)
// ---------------------------------------------------------------------------

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
};

export function getModelsForProvider(provider: string): KnownModel[] {
  return PROVIDER_MODELS[provider.toLowerCase()] ?? [];
}

// ---------------------------------------------------------------------------
// Model resolution priority (AC-430 Phase 3)
// ---------------------------------------------------------------------------

export interface ResolveModelOpts {
  cliModel?: string;
  projectModel?: string;
  envModel?: string;
  configDir: string;
  provider: string;
}

export function resolveModel(opts: ResolveModelOpts): string | undefined {
  // 1. CLI flag (highest)
  if (opts.cliModel) return opts.cliModel;

  // 2. Project config
  if (opts.projectModel) return opts.projectModel;

  // 3. Environment variable
  if (opts.envModel) return opts.envModel;

  // 4. Stored credential default
  const stored = loadProviderCredentials(opts.configDir, opts.provider);
  if (stored?.model) return stored.model;

  // 5. First known model for provider
  const models = getModelsForProvider(opts.provider);
  if (models.length > 0) return models[0].id;

  return undefined;
}

// ---------------------------------------------------------------------------
// Auth-aware model listing (AC-430 Phase 3)
// ---------------------------------------------------------------------------

export interface AuthenticatedModel {
  provider: string;
  modelId: string;
  displayName: string;
}

export function listAuthenticatedModels(configDir: string): AuthenticatedModel[] {
  const discovered = discoverAllProviders(configDir);
  const authed = discovered.filter((d) => d.hasApiKey || !getKnownProvider(d.provider)?.requiresKey);
  const result: AuthenticatedModel[] = [];

  for (const provider of authed) {
    const models = getModelsForProvider(provider.provider);
    for (const model of models) {
      result.push({
        provider: provider.provider,
        modelId: model.id,
        displayName: model.displayName,
      });
    }
  }

  return result;
}

// ---------------------------------------------------------------------------
// API key format validation
// ---------------------------------------------------------------------------

const KEY_FORMAT_RULES: Record<string, { prefix?: string; label: string }> = {};

// Build rules from the known providers registry
for (const p of KNOWN_PROVIDERS) {
  if (p.keyPrefix) {
    KEY_FORMAT_RULES[p.id] = { prefix: p.keyPrefix, label: p.displayName };
  }
}

const NO_KEY_PROVIDERS = new Set(
  KNOWN_PROVIDERS.filter((p) => !p.requiresKey).map((p) => p.id),
);

export async function validateApiKey(
  provider: string,
  apiKey: string,
): Promise<ValidationResult> {
  const normalized = provider.toLowerCase();

  // Providers that don't need keys
  if (NO_KEY_PROVIDERS.has(normalized)) {
    return { valid: true };
  }

  // Empty key check
  if (!apiKey) {
    return { valid: false, error: `API key is empty for ${provider}` };
  }

  // Format check for known providers
  const rule = KEY_FORMAT_RULES[normalized];
  if (rule?.prefix && !apiKey.startsWith(rule.prefix)) {
    return {
      valid: false,
      error: `Invalid ${rule.label} API key format: expected '${rule.prefix}...' prefix`,
    };
  }

  // Unknown providers: any non-empty key is accepted
  return { valid: true };
}
