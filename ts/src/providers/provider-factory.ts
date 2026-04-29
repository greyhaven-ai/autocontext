import { ProviderError } from "../types/index.js";
import type { CompletionResult, LLMProvider } from "../types/index.js";
import { DeterministicProvider } from "./deterministic.js";
import { ClaudeCLIRuntime } from "../runtimes/claude-cli.js";
import { CodexCLIRuntime, CodexCLIConfig } from "../runtimes/codex-cli.js";
import { PiCLIRuntime, PiCLIConfig } from "../runtimes/pi-cli.js";
import { PiPersistentRPCRuntime, PiRPCRuntime, PiRPCConfig } from "../runtimes/pi-rpc.js";
import { RuntimeBridgeProvider } from "../agents/provider-bridge.js";
import { SUPPORTED_PROVIDER_TYPES } from "./supported-provider-types.js";

export { SUPPORTED_PROVIDER_TYPES } from "./supported-provider-types.js";

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
      } satisfies CompletionResult;
    },
  };
}

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
      } satisfies CompletionResult;
    },
  };
}

export const OPENAI_COMPATIBLE_PROVIDER_DEFAULTS: Record<
  string,
  {
    baseUrl?: string;
    envVar: string;
    defaultModel: string;
  }
> = {
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

export interface CreateProviderOpts {
  providerType: string;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
  claudeModel?: string;
  claudeFallbackModel?: string;
  claudeTools?: string;
  claudePermissionMode?: string;
  claudeSessionPersistence?: boolean;
  claudeTimeout?: number;
  codexModel?: string;
  codexApprovalMode?: string;
  codexTimeout?: number;
  codexWorkspace?: string;
  codexQuiet?: boolean;
  piCommand?: string;
  piTimeout?: number;
  piWorkspace?: string;
  piModel?: string;
  piNoContextFiles?: boolean;
  piRpcEndpoint?: string;
  piRpcApiKey?: string;
  piRpcSessionPersistence?: boolean;
  piRpcPersistent?: boolean;
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
    const inner = createOpenAICompatibleProvider({
      apiKey: opts.apiKey ?? "no-key",
      baseUrl: opts.baseUrl ?? "http://localhost:8080/v1",
      model: opts.model ?? "hermes-3-llama-3.1-8b",
    });
    return { ...inner, name: "hermes-gateway" };
  }

  if (type === "claude-cli") {
    const resolvedModel = opts.claudeModel ?? opts.model;
    const runtime = new ClaudeCLIRuntime({
      model: resolvedModel,
      fallbackModel: opts.claudeFallbackModel,
      tools: opts.claudeTools,
      permissionMode: opts.claudePermissionMode,
      sessionPersistence: opts.claudeSessionPersistence,
      timeout: opts.claudeTimeout ? opts.claudeTimeout * 1000 : undefined,
    });
    return new RuntimeBridgeProvider(runtime as never, resolvedModel ?? "sonnet");
  }

  if (type === "codex") {
    const resolvedModel = opts.codexModel ?? opts.model;
    const runtime = new CodexCLIRuntime(
      new CodexCLIConfig({
        model: resolvedModel,
        approvalMode: opts.codexApprovalMode,
        timeout: opts.codexTimeout,
        workspace: opts.codexWorkspace,
        quiet: opts.codexQuiet,
      }),
    );
    return new RuntimeBridgeProvider(runtime as never, resolvedModel ?? "o4-mini");
  }

  if (type === "pi") {
    const resolvedModel = opts.model ?? opts.piModel;
    const runtime = new PiCLIRuntime(
      new PiCLIConfig({
        piCommand: opts.piCommand,
        timeout: opts.piTimeout,
        workspace: opts.piWorkspace,
        model: resolvedModel,
        noContextFiles: opts.piNoContextFiles,
      }),
    );
    return new RuntimeBridgeProvider(runtime as never, resolvedModel ?? "pi-default");
  }

  if (type === "pi-rpc") {
    const resolvedModel = opts.model ?? opts.piModel;
    const Runtime = opts.piRpcPersistent ? PiPersistentRPCRuntime : PiRPCRuntime;
    const runtime = new Runtime(
      new PiRPCConfig({
        piCommand: opts.piCommand,
        model: resolvedModel,
        timeout: opts.piTimeout,
        workspace: opts.piWorkspace,
        sessionPersistence: opts.piRpcSessionPersistence,
        noContextFiles: opts.piNoContextFiles,
      }),
    );
    return new RuntimeBridgeProvider(runtime as never, resolvedModel ?? "pi-rpc-default");
  }

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
    `Unknown provider type: ${JSON.stringify(type)}. Supported: ${SUPPORTED_PROVIDER_TYPES.join(", ")}`,
  );
}
