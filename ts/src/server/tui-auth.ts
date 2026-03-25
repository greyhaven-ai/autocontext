/**
 * TUI auth command handlers (AC-408).
 *
 * Shared credential store operations for /login, /logout, /provider, /whoami
 * TUI commands. Uses the same credential store as `autoctx login` (CLI).
 */

import {
  saveProviderCredentials,
  loadProviderCredentials,
  removeProviderCredentials,
  listConfiguredProviders,
  validateApiKey,
  CREDENTIALS_FILE,
} from "../config/credentials.js";
import { existsSync, unlinkSync } from "node:fs";
import { join } from "node:path";

export interface TuiLoginResult {
  saved: boolean;
  provider: string;
  validationWarning?: string;
}

export interface TuiAuthStatus {
  provider: string;
  authenticated: boolean;
  model?: string;
  configuredProviders?: Array<{ provider: string; hasApiKey: boolean }>;
}

/** Active provider override for TUI session (not persisted to disk). */
let activeProviderOverride: string | null = null;

export async function handleTuiLogin(
  configDir: string,
  provider: string,
  apiKey?: string,
  model?: string,
  baseUrl?: string,
): Promise<TuiLoginResult> {
  let validationWarning: string | undefined;

  if (apiKey) {
    const validation = await validateApiKey(provider, apiKey);
    if (!validation.valid) {
      validationWarning = validation.error;
    }
  }

  const creds: Record<string, string | undefined> = {};
  if (apiKey) creds.apiKey = apiKey;
  if (model) creds.model = model;
  if (baseUrl) creds.baseUrl = baseUrl;

  saveProviderCredentials(configDir, provider, creds);
  activeProviderOverride = provider;

  return {
    saved: true,
    provider,
    ...(validationWarning ? { validationWarning } : {}),
  };
}

export function handleTuiLogout(configDir: string, provider?: string): void {
  if (provider) {
    removeProviderCredentials(configDir, provider);
    if (activeProviderOverride === provider) {
      activeProviderOverride = null;
    }
  } else {
    // Clear entire credential file
    const credPath = join(configDir, CREDENTIALS_FILE);
    if (existsSync(credPath)) {
      unlinkSync(credPath);
    }
    activeProviderOverride = null;
  }
}

export function handleTuiSwitchProvider(configDir: string, provider: string): void {
  activeProviderOverride = provider;
}

export function handleTuiWhoami(configDir: string): TuiAuthStatus {
  const configured = listConfiguredProviders(configDir);

  // Determine active provider
  let activeProvider = activeProviderOverride;
  if (!activeProvider && configured.length > 0) {
    activeProvider = configured[0].provider;
  }

  if (!activeProvider) {
    return { provider: "none", authenticated: false, configuredProviders: [] };
  }

  const creds = loadProviderCredentials(configDir, activeProvider);
  return {
    provider: activeProvider,
    authenticated: Boolean(creds?.apiKey),
    ...(creds?.model ? { model: creds.model } : {}),
    configuredProviders: configured.map((c) => ({
      provider: c.provider,
      hasApiKey: c.hasApiKey,
    })),
  };
}
