/**
 * Provider module — pluggable LLM provider construction.
 *
 * Supports Anthropic, OpenAI-compatible (OpenAI, OpenRouter, vLLM, Ollama).
 * Uses native fetch() — no external SDK dependency required.
 */

import { ProviderError } from "../types/index.js";
import type { CompletionResult, LLMProvider } from "../types/index.js";
import { DeterministicProvider } from "./deterministic.js";

// ---------------------------------------------------------------------------
// Anthropic Provider
// ---------------------------------------------------------------------------

export interface AnthropicProviderOpts {
  apiKey: string;
  model?: string;
}

export function createAnthropicProvider(opts: AnthropicProviderOpts): LLMProvider {
  const defaultModel = opts.model || "claude-sonnet-4-20250514";

  return {
    name: "anthropic",
    defaultModel: () => defaultModel,
    complete: async (callOpts) => {
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": opts.apiKey,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model: callOpts.model || defaultModel,
          max_tokens: callOpts.maxTokens ?? 4096,
          temperature: callOpts.temperature ?? 0,
          system: callOpts.systemPrompt,
          messages: [{ role: "user", content: callOpts.userPrompt }],
        }),
      });

      if (!res.ok) {
        const body = await res.text();
        throw new ProviderError(`Anthropic API error ${res.status}: ${body.slice(0, 200)}`);
      }

      const data = (await res.json()) as {
        content: Array<{ type: string; text: string }>;
        model: string;
        usage: { input_tokens: number; output_tokens: number };
      };

      const text = data.content
        .filter((c) => c.type === "text")
        .map((c) => c.text)
        .join("");

      return {
        text,
        model: data.model,
        usage: { input: data.usage.input_tokens, output: data.usage.output_tokens },
      };
    },
  };
}

// ---------------------------------------------------------------------------
// OpenAI-Compatible Provider
// ---------------------------------------------------------------------------

export interface OpenAICompatibleProviderOpts {
  apiKey?: string;
  baseUrl?: string;
  model?: string;
}

export function createOpenAICompatibleProvider(opts: OpenAICompatibleProviderOpts): LLMProvider {
  const defaultModel = opts.model || "gpt-4o";
  const baseUrl = (opts.baseUrl ?? "https://api.openai.com/v1").replace(/\/+$/, "");
  const apiKey = opts.apiKey ?? "";

  return {
    name: "openai-compatible",
    defaultModel: () => defaultModel,
    complete: async (callOpts) => {
      const res = await fetch(`${baseUrl}/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model: callOpts.model || defaultModel,
          max_tokens: callOpts.maxTokens ?? 4096,
          temperature: callOpts.temperature ?? 0,
          messages: [
            { role: "system", content: callOpts.systemPrompt },
            { role: "user", content: callOpts.userPrompt },
          ],
        }),
      });

      if (!res.ok) {
        const body = await res.text();
        throw new ProviderError(`OpenAI API error ${res.status}: ${body.slice(0, 200)}`);
      }

      const data = (await res.json()) as {
        choices: Array<{ message: { content: string } }>;
        model: string;
        usage: { prompt_tokens: number; completion_tokens: number };
      };

      const text = data.choices[0]?.message?.content ?? "";
      return {
        text,
        model: data.model,
        usage: { input: data.usage.prompt_tokens, output: data.usage.completion_tokens },
      };
    },
  };
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export interface CreateProviderOpts {
  providerType: string;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
}

export function createProvider(opts: CreateProviderOpts): LLMProvider {
  const type = opts.providerType.toLowerCase().trim();

  if (type === "anthropic") {
    return createAnthropicProvider({
      apiKey: opts.apiKey ?? "",
      model: opts.model,
    });
  }

  if (type === "openai" || type === "openai-compatible") {
    return createOpenAICompatibleProvider({
      apiKey: opts.apiKey,
      baseUrl: opts.baseUrl,
      model: opts.model,
    });
  }

  if (type === "ollama") {
    return createOpenAICompatibleProvider({
      apiKey: "ollama",
      baseUrl: opts.baseUrl ?? "http://localhost:11434/v1",
      model: opts.model ?? "llama3.1",
    });
  }

  if (type === "vllm") {
    return createOpenAICompatibleProvider({
      apiKey: opts.apiKey ?? "no-key",
      baseUrl: opts.baseUrl ?? "http://localhost:8000/v1",
      model: opts.model ?? "default",
    });
  }

  if (type === "hermes") {
    // Hermes gateway via OpenAI-compatible API with hermes-specific defaults
    const inner = createOpenAICompatibleProvider({
      apiKey: opts.apiKey ?? "no-key",
      baseUrl: opts.baseUrl ?? "http://localhost:8080/v1",
      model: opts.model ?? "hermes-3-llama-3.1-8b",
    });
    return { ...inner, name: "hermes-gateway" };
  }

  if (type === "deterministic") {
    return new DeterministicProvider();
  }

  throw new ProviderError(
    `Unknown provider type: ${JSON.stringify(type)}. Supported: anthropic, openai, openai-compatible, ollama, vllm, hermes, deterministic`,
  );
}

// ---------------------------------------------------------------------------
// Environment-based config resolution (for CLI)
// ---------------------------------------------------------------------------

export interface ProviderConfig {
  providerType: string;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
}

export function resolveProviderConfig(overrides: Partial<ProviderConfig> = {}): ProviderConfig {
  // Python-compatible: AUTOCONTEXT_AGENT_PROVIDER takes precedence
  const providerType =
    overrides.providerType ??
    process.env.AUTOCONTEXT_AGENT_PROVIDER ??
    process.env.AUTOCONTEXT_PROVIDER ??
    "anthropic";
  // Agent-specific env vars (Python-compatible) with fallback to generic
  const model =
    overrides.model ??
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL ??
    process.env.AUTOCONTEXT_MODEL;
  const baseUrl =
    overrides.baseUrl ??
    process.env.AUTOCONTEXT_AGENT_BASE_URL ??
    process.env.AUTOCONTEXT_BASE_URL;
  const genericKey =
    overrides.apiKey ??
    process.env.AUTOCONTEXT_AGENT_API_KEY ??
    process.env.AUTOCONTEXT_API_KEY;

  const type = providerType.toLowerCase().trim();

  if (type === "deterministic") {
    return { providerType: type, model };
  }

  if (type === "anthropic") {
    const apiKey = genericKey ?? process.env.ANTHROPIC_API_KEY;
    if (!apiKey) {
      throw new ProviderError(
        "ANTHROPIC_API_KEY environment variable required (or set AUTOCONTEXT_API_KEY / AUTOCONTEXT_AGENT_API_KEY)",
      );
    }
    return { providerType: type, apiKey, model, baseUrl };
  }

  if (type === "ollama") {
    return {
      providerType: type,
      apiKey: genericKey ?? "ollama",
      baseUrl: baseUrl ?? "http://localhost:11434/v1",
      model: model ?? "llama3.1",
    };
  }

  if (type === "vllm") {
    return {
      providerType: type,
      apiKey: genericKey ?? "no-key",
      baseUrl: baseUrl ?? "http://localhost:8000/v1",
      model: model ?? "default",
    };
  }

  if (type === "hermes") {
    return {
      providerType: type,
      apiKey: genericKey ?? "no-key",
      baseUrl: baseUrl ?? "http://localhost:8080/v1",
      model: model ?? "hermes-3-llama-3.1-8b",
    };
  }

  // openai, openai-compatible, and other generic types
  const apiKey = genericKey ?? process.env.OPENAI_API_KEY;
  if (!apiKey) {
    throw new ProviderError(
      "API key required: set AUTOCONTEXT_API_KEY, AUTOCONTEXT_AGENT_API_KEY, or OPENAI_API_KEY",
    );
  }
  return { providerType: type, apiKey, baseUrl, model };
}

export function createConfiguredProvider(overrides: Partial<ProviderConfig> = {}): {
  provider: LLMProvider;
  config: ProviderConfig;
} {
  const config = resolveProviderConfig(overrides);
  return {
    provider: createProvider(config),
    config,
  };
}

export type GenerationRole = "competitor" | "analyst" | "coach" | "architect" | "curator";

export interface RoleProviderSettings {
  agentProvider: string;
  competitorProvider?: string;
  analystProvider?: string;
  coachProvider?: string;
  architectProvider?: string;
  modelCompetitor?: string;
  modelAnalyst?: string;
  modelCoach?: string;
  modelArchitect?: string;
  modelCurator?: string;
}

export interface RoleProviderBundle {
  defaultProvider: LLMProvider;
  defaultConfig: ProviderConfig;
  roleProviders: Partial<Record<GenerationRole, LLMProvider>>;
  roleModels: Partial<Record<GenerationRole, string>>;
}

export function buildRoleProviderBundle(
  settings: RoleProviderSettings,
  overrides: Partial<ProviderConfig> = {},
): RoleProviderBundle {
  const defaultConfig = resolveProviderConfig({
    ...overrides,
    providerType: overrides.providerType ?? settings.agentProvider,
  });
  const defaultProvider = createProvider(defaultConfig);

  const roleConfigs: Record<GenerationRole, ProviderConfig> = {
    competitor: resolveProviderConfig({
      ...overrides,
      providerType: settings.competitorProvider || defaultConfig.providerType,
      model: settings.modelCompetitor ?? defaultConfig.model,
    }),
    analyst: resolveProviderConfig({
      ...overrides,
      providerType: settings.analystProvider || defaultConfig.providerType,
      model: settings.modelAnalyst ?? defaultConfig.model,
    }),
    coach: resolveProviderConfig({
      ...overrides,
      providerType: settings.coachProvider || defaultConfig.providerType,
      model: settings.modelCoach ?? defaultConfig.model,
    }),
    architect: resolveProviderConfig({
      ...overrides,
      providerType: settings.architectProvider || defaultConfig.providerType,
      model: settings.modelArchitect ?? defaultConfig.model,
    }),
    curator: resolveProviderConfig({
      ...overrides,
      providerType: defaultConfig.providerType,
      model: settings.modelCurator ?? defaultConfig.model,
    }),
  };

  return {
    defaultProvider,
    defaultConfig,
    roleProviders: {
      competitor: createProvider(roleConfigs.competitor),
      analyst: createProvider(roleConfigs.analyst),
      coach: createProvider(roleConfigs.coach),
      architect: createProvider(roleConfigs.architect),
      curator: createProvider(roleConfigs.curator),
    },
    roleModels: {
      competitor: roleConfigs.competitor.model,
      analyst: roleConfigs.analyst.model,
      coach: roleConfigs.coach.model,
      architect: roleConfigs.architect.model,
      curator: roleConfigs.curator.model,
    },
  };
}
