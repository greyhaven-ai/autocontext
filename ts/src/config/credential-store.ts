import { execFileSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
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

export interface CredentialStore {
  providers: Record<string, ProviderCredentials>;
}

export function resolveApiKeyValue(value: string): string {
  if (!value || !value.startsWith("!")) {
    return value;
  }

  const command = value.slice(1).trim();
  const result = execFileSync("/bin/sh", ["-c", command], {
    encoding: "utf-8",
    timeout: 10_000,
    stdio: ["pipe", "pipe", "pipe"],
  });
  return result.trim();
}

function isLegacyCredentialStore(
  data: Record<string, unknown>,
): data is Record<string, unknown> & { provider: string } {
  return typeof data.provider === "string" && !data.providers;
}

function readLegacyProviderCredentials(
  data: Record<string, unknown> & { provider: string },
): CredentialStore {
  const provider = data.provider.trim();
  const credentials: ProviderCredentials = {};
  if (typeof data.apiKey === "string" && data.apiKey.trim()) {
    credentials.apiKey = data.apiKey.trim();
  }
  if (typeof data.model === "string" && data.model.trim()) {
    credentials.model = data.model.trim();
  }
  if (typeof data.baseUrl === "string" && data.baseUrl.trim()) {
    credentials.baseUrl = data.baseUrl.trim();
  }
  if (typeof data.savedAt === "string" && data.savedAt.trim()) {
    credentials.savedAt = data.savedAt.trim();
  }
  return { providers: { [provider]: credentials } };
}

function normalizeMultiProviderStore(data: Record<string, unknown>): CredentialStore {
  const providers: Record<string, ProviderCredentials> = {};
  const rawProviders = (data.providers ?? {}) as Record<string, Record<string, unknown>>;

  for (const [name, entry] of Object.entries(rawProviders)) {
    const credentials: ProviderCredentials = {};
    if (typeof entry.apiKey === "string") credentials.apiKey = entry.apiKey;
    if (typeof entry.model === "string") credentials.model = entry.model;
    if (typeof entry.baseUrl === "string") credentials.baseUrl = entry.baseUrl;
    if (typeof entry.savedAt === "string") credentials.savedAt = entry.savedAt;
    providers[name] = credentials;
  }

  return { providers };
}

export function readCredentialStore(configDir: string): CredentialStore {
  const filePath = join(configDir, CREDENTIALS_FILE);
  if (!existsSync(filePath)) {
    return { providers: {} };
  }

  const raw = JSON.parse(readFileSync(filePath, "utf-8")) as Record<string, unknown>;
  if (isLegacyCredentialStore(raw)) {
    return readLegacyProviderCredentials(raw);
  }

  return normalizeMultiProviderStore(raw);
}

export function writeCredentialStore(
  configDir: string,
  store: CredentialStore,
): void {
  mkdirSync(configDir, { recursive: true });
  const filePath = join(configDir, CREDENTIALS_FILE);
  writeFileSync(filePath, JSON.stringify(store, null, 2), "utf-8");
  chmodSync(filePath, 0o600);
}

export function saveProviderCredentials(
  configDir: string,
  provider: string,
  credentials: Omit<ProviderCredentials, "savedAt">,
): void {
  const store = readCredentialStore(configDir);
  store.providers[provider] = {
    ...credentials,
    savedAt: new Date().toISOString(),
  };
  writeCredentialStore(configDir, store);
}

export function loadProviderCredentials(
  configDir: string,
  provider: string,
): ProviderCredentials | null {
  const store = readCredentialStore(configDir);
  return store.providers[provider] ?? null;
}

export function listConfiguredProviders(configDir: string): ProviderAuthStatus[] {
  const store = readCredentialStore(configDir);
  return Object.entries(store.providers).map(([provider, credentials]) => ({
    provider,
    hasApiKey: Boolean(credentials.apiKey),
    ...(credentials.model ? { model: credentials.model } : {}),
    ...(credentials.baseUrl ? { baseUrl: credentials.baseUrl } : {}),
    ...(credentials.savedAt ? { savedAt: credentials.savedAt } : {}),
  }));
}

export function removeProviderCredentials(
  configDir: string,
  provider: string,
): boolean {
  const store = readCredentialStore(configDir);
  if (!(provider in store.providers)) {
    return false;
  }
  delete store.providers[provider];
  writeCredentialStore(configDir, store);
  return true;
}
