import {
  KNOWN_PROVIDERS,
  type KnownProvider,
} from "./credential-provider-registry.js";

export interface ValidationResult {
  valid: boolean;
  error?: string;
}

const KEY_FORMAT_RULES: Record<string, { prefix?: string; label: string }> = {};
for (const provider of KNOWN_PROVIDERS) {
  if (provider.keyPrefix) {
    KEY_FORMAT_RULES[provider.id] = {
      prefix: provider.keyPrefix,
      label: provider.displayName,
    };
  }
}

const NO_KEY_PROVIDERS = new Set(
  KNOWN_PROVIDERS.filter((provider) => !provider.requiresKey).map(
    (provider: KnownProvider) => provider.id,
  ),
);

export function validateApiKeyValue(
  provider: string,
  apiKey: string,
): ValidationResult {
  const normalizedProvider = provider.toLowerCase();

  if (NO_KEY_PROVIDERS.has(normalizedProvider)) {
    return { valid: true };
  }

  if (!apiKey) {
    return { valid: false, error: `API key is empty for ${provider}` };
  }

  const rule = KEY_FORMAT_RULES[normalizedProvider];
  if (rule?.prefix && !apiKey.startsWith(rule.prefix)) {
    return {
      valid: false,
      error: `Invalid ${rule.label} API key format: expected '${rule.prefix}...' prefix`,
    };
  }

  return { valid: true };
}

export async function validateApiKey(
  provider: string,
  apiKey: string,
): Promise<ValidationResult> {
  return validateApiKeyValue(provider, apiKey);
}
