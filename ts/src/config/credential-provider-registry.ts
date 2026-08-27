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
