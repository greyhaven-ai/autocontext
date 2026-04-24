export const SUPPORTED_PROVIDER_TYPES = [
  "anthropic",
  "openai",
  "openai-compatible",
  "ollama",
  "vllm",
  "hermes",
  "gemini",
  "mistral",
  "groq",
  "openrouter",
  "azure-openai",
  "claude-cli",
  "codex",
  "pi",
  "pi-rpc",
  "deterministic",
] as const;

export type SupportedProviderType = typeof SUPPORTED_PROVIDER_TYPES[number];
