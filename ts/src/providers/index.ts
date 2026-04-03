/**
 * Provider module — pluggable LLM provider construction.
 *
 * Supports Anthropic, OpenAI-compatible (OpenAI, OpenRouter, vLLM, Ollama).
 * Uses native fetch() — no external SDK dependency required.
 */

import { ProviderError } from "../types/index.js";
import type { CompletionResult, LLMProvider } from "../types/index.js";
import { getKnownProvider, loadPersistedCredentials, loadProjectConfig } from "../config/index.js";
import { DeterministicProvider } from "./deterministic.js";
import { PiCLIRuntime, PiCLIConfig } from "../runtimes/pi-cli.js";
import { PiRPCRuntime, PiRPCConfig } from "../runtimes/pi-rpc.js";
import { RuntimeBridgeProvider } from "../agents/provider-bridge.js";

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

const OPENAI_COMPATIBLE_PROVIDER_DEFAULTS: Record<string, {
  baseUrl?: string;
  envVar: string;
  defaultModel: string;
}> = {
  gemini: {
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    envVar: "GEMINI_API_KEY",
    defaultModel: "gemini-2.5-pro",
  },
  mistral: {
    baseUrl: "https://api.mistral.ai/v1",
    envVar: "MISTRAL_API_KEY",
    defaultModel: "mistral-large-latest",
  },
  groq: {
    baseUrl: "https://api.groq.com/openai/v1",
    envVar: "GROQ_API_KEY",
    defaultModel: "llama-3.3-70b-versatile",
  },
  openrouter: {
    baseUrl: "https://openrouter.ai/api/v1",
    envVar: "OPENROUTER_API_KEY",
    defaultModel: "anthropic/claude-sonnet-4",
  },
  "azure-openai": {
    envVar: "AZURE_OPENAI_API_KEY",
    defaultModel: "gpt-4o",
  },
};

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export interface CreateProviderOpts {
  providerType: string;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
  piCommand?: string;
  piTimeout?: number;
  piWorkspace?: string;
  piModel?: string;
  piRpcEndpoint?: string;
  piRpcApiKey?: string;
  piRpcSessionPersistence?: boolean;
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

  if (type === "pi") {
    const resolvedModel = opts.model ?? opts.piModel;
    const runtime = new PiCLIRuntime(new PiCLIConfig({
      piCommand: opts.piCommand,
      timeout: opts.piTimeout,
      workspace: opts.piWorkspace,
      model: resolvedModel,
    }));
    return new RuntimeBridgeProvider(runtime as any, resolvedModel ?? "pi-default");
  }

  if (type === "pi-rpc") {
    const runtime = new PiRPCRuntime(new PiRPCConfig({
      endpoint: opts.piRpcEndpoint ?? opts.baseUrl,
      apiKey: opts.piRpcApiKey ?? opts.apiKey,
      sessionPersistence: opts.piRpcSessionPersistence,
    }));
    return new RuntimeBridgeProvider(runtime as any, opts.model ?? "pi-rpc-default");
  }

  // OpenAI-compatible providers with per-service defaults
  const compat = OPENAI_COMPATIBLE_PROVIDER_DEFAULTS[type];
  if (compat) {
    return createOpenAICompatibleProvider({
      apiKey: opts.apiKey ?? process.env[compat.envVar] ?? "",
      baseUrl: opts.baseUrl ?? compat.baseUrl,
      model: opts.model ?? compat.defaultModel,
    });
  }

  if (type === "deterministic") {
    return new DeterministicProvider();
  }

  throw new ProviderError(
    `Unknown provider type: ${JSON.stringify(type)}. Supported: anthropic, openai, openai-compatible, ollama, vllm, hermes, gemini, mistral, groq, openrouter, azure-openai, pi, pi-rpc, deterministic`,
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

interface ResolveProviderConfigOpts {
  preferProviderOverride?: boolean;
  preferModelOverride?: boolean;
}

export function resolveProviderConfig(
  overrides: Partial<ProviderConfig> = {},
  opts: ResolveProviderConfigOpts = {},
): ProviderConfig {
  const projectConfig = loadProjectConfig();
  const defaultPersistedCredentials = loadPersistedCredentials();
  const envProviderType =
    process.env.AUTOCONTEXT_AGENT_PROVIDER ??
    process.env.AUTOCONTEXT_PROVIDER;
  const envModel =
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL ??
    process.env.AUTOCONTEXT_MODEL;

  // Python-compatible: AUTOCONTEXT_AGENT_PROVIDER takes precedence
  const providerType =
    (opts.preferProviderOverride ? overrides.providerType : undefined) ??
    envProviderType ??
    overrides.providerType ??
    projectConfig?.provider ??
    defaultPersistedCredentials?.provider ??
    "anthropic";
  const persistedCredentials = loadPersistedCredentials(undefined, providerType);
  // Agent-specific env vars (Python-compatible) with fallback to generic
  const model =
    (opts.preferModelOverride ? overrides.model : undefined) ??
    envModel ??
    overrides.model ??
    projectConfig?.model ??
    persistedCredentials?.model;
  const baseUrl =
    process.env.AUTOCONTEXT_AGENT_BASE_URL ??
    process.env.AUTOCONTEXT_BASE_URL ??
    overrides.baseUrl ??
    persistedCredentials?.baseUrl;
  const genericKey =
    process.env.AUTOCONTEXT_AGENT_API_KEY ??
    process.env.AUTOCONTEXT_API_KEY ??
    overrides.apiKey ??
    persistedCredentials?.apiKey;

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

  if (type === "pi" || type === "pi-rpc") {
    return { providerType: type, apiKey: genericKey, baseUrl, model };
  }

  const providerSpecificEnvVar =
    OPENAI_COMPATIBLE_PROVIDER_DEFAULTS[type]?.envVar ??
    getKnownProvider(type)?.envVar;
  const providerSpecificKey = providerSpecificEnvVar
    ? process.env[providerSpecificEnvVar]
    : undefined;
  const openaiFallbackKey =
    type === "openai" || type === "openai-compatible"
      ? process.env.OPENAI_API_KEY
      : undefined;
  const apiKey = genericKey ?? providerSpecificKey ?? openaiFallbackKey;
  if (!apiKey) {
    const keyVars = [
      "AUTOCONTEXT_API_KEY",
      "AUTOCONTEXT_AGENT_API_KEY",
    ];
    if (providerSpecificEnvVar) {
      keyVars.push(providerSpecificEnvVar);
    } else if (type === "openai" || type === "openai-compatible") {
      keyVars.push("OPENAI_API_KEY");
    }
    throw new ProviderError(
      `API key required: set ${keyVars.join(", or ")}`,
    );
  }
  return { providerType: type, apiKey, baseUrl, model };
}

export function createConfiguredProvider(
  overrides: Partial<ProviderConfig> = {},
  settings: Partial<RoleProviderSettings> = {},
): {
  provider: LLMProvider;
  config: ProviderConfig;
} {
  const config = resolveProviderConfig(overrides);
  return {
    provider: createProvider(withRuntimeSettings(config, settings)),
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
  piCommand?: string;
  piTimeout?: number;
  piWorkspace?: string;
  piModel?: string;
  piRpcEndpoint?: string;
  piRpcApiKey?: string;
  piRpcSessionPersistence?: boolean;
}

export interface RoleProviderBundle {
  defaultProvider: LLMProvider;
  defaultConfig: ProviderConfig;
  roleProviders: Partial<Record<GenerationRole, LLMProvider>>;
  roleModels: Partial<Record<GenerationRole, string>>;
}

function withRuntimeSettings(
  config: ProviderConfig,
  settings: Partial<RoleProviderSettings> = {},
): CreateProviderOpts {
  return {
    ...config,
    piCommand: settings.piCommand,
    piTimeout: settings.piTimeout,
    piWorkspace: settings.piWorkspace,
    piModel: settings.piModel,
    piRpcEndpoint: settings.piRpcEndpoint,
    piRpcApiKey: settings.piRpcApiKey,
    piRpcSessionPersistence: settings.piRpcSessionPersistence,
  };
}

export function buildRoleProviderBundle(
  settings: RoleProviderSettings,
  overrides: Partial<ProviderConfig> = {},
): RoleProviderBundle {
  const defaultConfig = resolveProviderConfig({
    ...overrides,
    providerType: overrides.providerType ?? settings.agentProvider,
  });
  const defaultProvider = createProvider(withRuntimeSettings(defaultConfig, settings));

  const roleConfigs: Record<GenerationRole, ProviderConfig> = {
    competitor: resolveProviderConfig({
      ...overrides,
      providerType: settings.competitorProvider || defaultConfig.providerType,
      model: settings.modelCompetitor ?? defaultConfig.model,
    }, {
      preferProviderOverride: Boolean(settings.competitorProvider),
      preferModelOverride: Boolean(settings.modelCompetitor),
    }),
    analyst: resolveProviderConfig({
      ...overrides,
      providerType: settings.analystProvider || defaultConfig.providerType,
      model: settings.modelAnalyst ?? defaultConfig.model,
    }, {
      preferProviderOverride: Boolean(settings.analystProvider),
      preferModelOverride: Boolean(settings.modelAnalyst),
    }),
    coach: resolveProviderConfig({
      ...overrides,
      providerType: settings.coachProvider || defaultConfig.providerType,
      model: settings.modelCoach ?? defaultConfig.model,
    }, {
      preferProviderOverride: Boolean(settings.coachProvider),
      preferModelOverride: Boolean(settings.modelCoach),
    }),
    architect: resolveProviderConfig({
      ...overrides,
      providerType: settings.architectProvider || defaultConfig.providerType,
      model: settings.modelArchitect ?? defaultConfig.model,
    }, {
      preferProviderOverride: Boolean(settings.architectProvider),
      preferModelOverride: Boolean(settings.modelArchitect),
    }),
    curator: resolveProviderConfig({
      ...overrides,
      providerType: defaultConfig.providerType,
      model: settings.modelCurator ?? defaultConfig.model,
    }, {
      preferModelOverride: Boolean(settings.modelCurator),
    }),
  };

  return {
    defaultProvider,
    defaultConfig,
    roleProviders: {
      competitor: createProvider(withRuntimeSettings(roleConfigs.competitor, settings)),
      analyst: createProvider(withRuntimeSettings(roleConfigs.analyst, settings)),
      coach: createProvider(withRuntimeSettings(roleConfigs.coach, settings)),
      architect: createProvider(withRuntimeSettings(roleConfigs.architect, settings)),
      curator: createProvider(withRuntimeSettings(roleConfigs.curator, settings)),
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
