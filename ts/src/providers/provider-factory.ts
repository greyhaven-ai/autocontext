import { ProviderError } from "../types/index.js";
import { clampOutputTokens } from "./token-caps.js";
import type {
  CompletionResult,
  LLMProvider,
} from "../types/index.js";
import { DeterministicProvider } from "./deterministic.js";
import {
  DEEP_THINK_DESCRIPTION,
  DEEP_THINK_PARAMETERS,
  DEEP_THINK_TOOL_NAME,
  addCompletionUsage,
  deepThinkAcknowledgement,
  extractDeepThought,
  isRecord,
  withDeepThinkInstruction,
} from "./thinking.js";
import { ClaudeCLIRuntime } from "../runtimes/claude-cli.js";
import { CodexCLIRuntime, CodexCLIConfig } from "../runtimes/codex-cli.js";
import { PiCLIRuntime, PiCLIConfig } from "../runtimes/pi-cli.js";
import { PiPersistentRPCRuntime, PiRPCRuntime, PiRPCConfig } from "../runtimes/pi-rpc.js";
import {
  RuntimeBridgeProvider,
  type RuntimeBridgeProviderOpts,
} from "../agents/provider-bridge.js";
import type { AgentRuntime } from "../runtimes/base.js";
import { SUPPORTED_PROVIDER_TYPES } from "./supported-provider-types.js";
import type { RuntimeCommandGrant } from "../runtimes/workspace-env.js";
import type { RuntimeSession } from "../session/runtime-session.js";

export { SUPPORTED_PROVIDER_TYPES } from "./supported-provider-types.js";

export interface AnthropicProviderOpts {
  apiKey: string;
  model?: string;
}

export function createAnthropicProvider(opts: AnthropicProviderOpts): LLMProvider {
  const defaultModel = opts.model || "claude-sonnet-4-20250514";

  const post = async (body: Record<string, unknown>): Promise<AnthropicMessageResponse> => {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": opts.apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const errorBody = await res.text();
      throw new ProviderError(`Anthropic API error ${res.status}: ${errorBody.slice(0, 200)}`);
    }
    return parseAnthropicMessageResponse(await res.json());
  };

  return {
    name: "anthropic",
    supportsThinkingStream: true,
    defaultModel: () => defaultModel,
    complete: async (callOpts) => {
      const model = callOpts.model || defaultModel;
      const data = await post({
        model,
        max_tokens: clampOutputTokens(callOpts.maxTokens ?? 4096, model),
        temperature: callOpts.temperature ?? 0,
        system: callOpts.systemPrompt,
        messages: [{ role: "user", content: callOpts.userPrompt }],
      });

      const text = data.content
        .filter((c) => c.type === "text")
        .map((c) => c.text ?? "")
        .join("");

      return {
        text,
        model: data.model,
        usage: { input: data.usage.input_tokens, output: data.usage.output_tokens },
        stopReason: data.stop_reason,
      } satisfies CompletionResult;
    },
    completeWithThinking: async (callOpts) => {
      const maxToolTurns = callOpts.maxToolTurns ?? 8;
      if (maxToolTurns < 1) throw new RangeError("maxToolTurns must be at least 1");

      const model = callOpts.model || defaultModel;
      const messages: Array<Record<string, unknown>> = [
        { role: "user", content: callOpts.userPrompt },
      ];
      const thinkingStream: string[] = [];
      const usage: Record<string, number> = {};

      for (let turn = 0; turn < maxToolTurns; turn++) {
        const toolChoice =
          turn === 0
            ? { type: "tool", name: DEEP_THINK_TOOL_NAME, disable_parallel_tool_use: true }
            : { type: "auto", disable_parallel_tool_use: true };
        const data = await post({
          model,
          max_tokens: clampOutputTokens(callOpts.maxTokens ?? 4096, model),
          temperature: callOpts.temperature ?? 0,
          system: withDeepThinkInstruction(callOpts.systemPrompt),
          messages,
          tools: [
            {
              name: DEEP_THINK_TOOL_NAME,
              description: DEEP_THINK_DESCRIPTION,
              input_schema: DEEP_THINK_PARAMETERS,
            },
          ],
          tool_choice: toolChoice,
        });
        addCompletionUsage(usage, {
          input: data.usage.input_tokens,
          output: data.usage.output_tokens,
        });

        const toolBlocks = data.content.filter((block) => block.type === "tool_use");
        if (toolBlocks.length === 0) {
          if (turn === 0) {
            throw new ProviderError("Anthropic API did not honor required deep_think tool choice");
          }
          return {
            text: data.content
              .filter((block) => block.type === "text")
              .map((block) => block.text ?? "")
              .join(""),
            model: data.model,
            usage,
            stopReason: data.stop_reason,
            constrained: false,
            thinkingStream,
            thinkingTool: DEEP_THINK_TOOL_NAME,
            thinkingCapture: "tool",
          } satisfies CompletionResult;
        }

        messages.push({ role: "assistant", content: data.content });
        const toolResults: Array<Record<string, unknown>> = [];
        for (const block of toolBlocks) {
          if (block.name !== DEEP_THINK_TOOL_NAME) {
            throw new ProviderError(
              `Unexpected thinking tool call: ${block.name || "<missing>"}`,
            );
          }
          thinkingStream.push(extractDeepThought(block.input));
          toolResults.push({
            type: "tool_result",
            tool_use_id: block.id ?? "",
            content: deepThinkAcknowledgement(thinkingStream.length),
          });
        }
        messages.push({ role: "user", content: toolResults });
      }
      throw new ProviderError(`Model exceeded ${maxToolTurns} deep_think tool turns`);
    },
  };
}

interface AnthropicContentBlock {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  input?: unknown;
}

interface AnthropicMessageResponse {
  content: AnthropicContentBlock[];
  model: string;
  usage: { input_tokens: number; output_tokens: number };
  stop_reason?: string;
}

function parseAnthropicMessageResponse(value: unknown): AnthropicMessageResponse {
  if (!isRecord(value) || !Array.isArray(value["content"])) {
    throw new ProviderError("Anthropic API returned a malformed message response");
  }
  const usage = value["usage"];
  if (
    typeof value["model"] !== "string" ||
    !isRecord(usage) ||
    typeof usage["input_tokens"] !== "number" ||
    typeof usage["output_tokens"] !== "number"
  ) {
    throw new ProviderError("Anthropic API returned malformed model or usage metadata");
  }
  const content = value["content"].map((raw): AnthropicContentBlock => {
    if (!isRecord(raw) || typeof raw["type"] !== "string") {
      throw new ProviderError("Anthropic API returned a malformed content block");
    }
    const block: AnthropicContentBlock = { type: raw["type"] };
    if (typeof raw["text"] === "string") block.text = raw["text"];
    if (typeof raw["id"] === "string") block.id = raw["id"];
    if (typeof raw["name"] === "string") block.name = raw["name"];
    if ("input" in raw) block.input = raw["input"];
    return block;
  });
  return {
    content,
    model: value["model"],
    usage: {
      input_tokens: usage["input_tokens"],
      output_tokens: usage["output_tokens"],
    },
    ...(typeof value["stop_reason"] === "string" ? { stop_reason: value["stop_reason"] } : {}),
  };
}

export interface OpenAICompatibleProviderOpts {
  apiKey?: string;
  baseUrl?: string;
  model?: string;
}

function isUnsupportedResponseFormatError(status: number, body: string): boolean {
  if (![400, 404, 422].includes(status)) return false;

  const message = body.toLowerCase();
  const mentionsSchema = message.includes("response_format") || message.includes("json_schema");
  const rejectsSchema = [
    "unsupported",
    "not supported",
    "unknown",
    "unrecognized",
    "invalid",
  ].some((token) => message.includes(token));
  return mentionsSchema && rejectsSchema;
}

function isUnsupportedReasoningEffortError(status: number, body: string): boolean {
  if (![400, 404, 422].includes(status)) return false;
  const message = body.toLowerCase();
  const mentionsField =
    message.includes("reasoning_effort") || message.includes("reasoning effort");
  const rejectsField = [
    "unsupported",
    "not supported",
    "unknown",
    "unrecognized",
    "invalid",
  ].some((token) => message.includes(token));
  return mentionsField && rejectsField;
}

export function createOpenAICompatibleProvider(opts: OpenAICompatibleProviderOpts): LLMProvider {
  const defaultModel = opts.model || "gpt-4o";
  const baseUrl = (opts.baseUrl ?? "https://api.openai.com/v1").replace(/\/+$/, "");
  const apiKey = opts.apiKey ?? "";

  return {
    name: "openai-compatible",
    supportsThinkingStream: true,
    defaultModel: () => defaultModel,
    complete: async (callOpts) => {
      const buildBody = (withSchema: boolean) =>
        JSON.stringify({
          model: callOpts.model || defaultModel,
          max_tokens: clampOutputTokens(callOpts.maxTokens ?? 4096, callOpts.model || defaultModel),
          temperature: callOpts.temperature ?? 0,
          messages: [
            { role: "system", content: callOpts.systemPrompt },
            { role: "user", content: callOpts.userPrompt },
          ],
          ...(withSchema && callOpts.outputSchema
            ? {
                response_format: {
                  type: "json_schema",
                  json_schema: {
                    name: callOpts.outputSchema.name,
                    strict: true,
                    schema: callOpts.outputSchema.schema,
                  },
                },
              }
            : {}),
        });

      const post = (withSchema: boolean) =>
        fetch(`${baseUrl}/chat/completions`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${apiKey}`,
          },
          body: buildBody(withSchema),
        });

      let constrained = Boolean(callOpts.outputSchema);
      let res = await post(constrained);

      if (!res.ok && constrained) {
        const body = await res.text();
        if (!isUnsupportedResponseFormatError(res.status, body)) {
          throw new ProviderError(`OpenAI API error ${res.status}: ${body.slice(0, 200)}`);
        }

        // This endpoint explicitly rejected response_format. Retry once
        // without it so a backend with no constrained-decoding support still
        // works, and report that the returned text was unconstrained.
        constrained = false;
        res = await post(false);
      }

      if (!res.ok) {
        const body = await res.text();
        throw new ProviderError(`OpenAI API error ${res.status}: ${body.slice(0, 200)}`);
      }

      const data = parseOpenAIChatResponse(await res.json());

      const text = data.choices[0]?.message?.content ?? "";
      return {
        text,
        model: data.model,
        usage: { input: data.usage.prompt_tokens, output: data.usage.completion_tokens },
        stopReason: data.choices[0]?.finish_reason,
        constrained,
      } satisfies CompletionResult;
    },
    completeWithThinking: async (callOpts) => {
      const maxToolTurns = callOpts.maxToolTurns ?? 8;
      if (maxToolTurns < 1) throw new RangeError("maxToolTurns must be at least 1");

      const model = callOpts.model || defaultModel;
      const messages: Array<Record<string, unknown>> = [
        { role: "system", content: withDeepThinkInstruction(callOpts.systemPrompt) },
        { role: "user", content: callOpts.userPrompt },
      ];
      const thinkingStream: string[] = [];
      const usage: Record<string, number> = {};
      let constrained = Boolean(callOpts.outputSchema);
      let reasoningEffortSupported = true;

      for (let turn = 0; turn < maxToolTurns; turn++) {
        const body: Record<string, unknown> = {
          model,
          max_tokens: clampOutputTokens(callOpts.maxTokens ?? 4096, model),
          temperature: callOpts.temperature ?? 0,
          messages,
          tools: [
            {
              type: "function",
              function: {
                name: DEEP_THINK_TOOL_NAME,
                description: DEEP_THINK_DESCRIPTION,
                parameters: DEEP_THINK_PARAMETERS,
              },
            },
          ],
          tool_choice: turn === 0 ? "required" : "auto",
          parallel_tool_calls: false,
        };
        if (reasoningEffortSupported) {
          body["reasoning_effort"] = callOpts.reasoningEffort ?? "none";
        }
        if (constrained && callOpts.outputSchema) {
          body["response_format"] = {
            type: "json_schema",
            json_schema: {
              name: callOpts.outputSchema.name,
              strict: true,
              schema: callOpts.outputSchema.schema,
            },
          };
        }

        let data: OpenAIChatResponse;
        while (true) {
          const res = await fetch(`${baseUrl}/chat/completions`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${apiKey}`,
            },
            body: JSON.stringify(body),
          });
          if (res.ok) {
            data = parseOpenAIChatResponse(await res.json());
            break;
          }

          const errorBody = await res.text();
          if (constrained && isUnsupportedResponseFormatError(res.status, errorBody)) {
            constrained = false;
            delete body["response_format"];
            continue;
          }
          if (
            "reasoning_effort" in body &&
            isUnsupportedReasoningEffortError(res.status, errorBody)
          ) {
            reasoningEffortSupported = false;
            delete body["reasoning_effort"];
            continue;
          }
          throw new ProviderError(`OpenAI API error ${res.status}: ${errorBody.slice(0, 200)}`);
        }

        addCompletionUsage(usage, {
          input: data.usage.prompt_tokens,
          output: data.usage.completion_tokens,
        });
        const choice = data.choices[0];
        if (!choice) throw new ProviderError("OpenAI-compatible API returned no completion choices");
        const toolCalls = choice.message.tool_calls ?? [];
        if (toolCalls.length === 0) {
          if (turn === 0) {
            throw new ProviderError(
              "OpenAI-compatible endpoint did not honor required deep_think tool choice",
            );
          }
          return {
            text: choice.message.content ?? "",
            model: data.model,
            usage,
            stopReason: choice.finish_reason,
            constrained,
            thinkingStream,
            thinkingTool: DEEP_THINK_TOOL_NAME,
            thinkingCapture: "tool",
          } satisfies CompletionResult;
        }

        messages.push({
          role: "assistant",
          content: choice.message.content ?? null,
          tool_calls: toolCalls,
        });
        for (const toolCall of toolCalls) {
          const name = toolCall.function?.name ?? "";
          if (name !== DEEP_THINK_TOOL_NAME) {
            throw new ProviderError(`Unexpected thinking tool call: ${name || "<missing>"}`);
          }
          thinkingStream.push(extractDeepThought(toolCall.function?.arguments));
          messages.push({
            role: "tool",
            tool_call_id: toolCall.id ?? "",
            content: deepThinkAcknowledgement(thinkingStream.length),
          });
        }
      }
      throw new ProviderError(`Model exceeded ${maxToolTurns} deep_think tool turns`);
    },
  };
}

interface OpenAIToolCall {
  id?: string;
  type?: string;
  function?: { name?: string; arguments?: unknown };
}

interface OpenAIChatResponse {
  choices: Array<{
    message: { content: string | null; tool_calls?: OpenAIToolCall[] };
    finish_reason?: string;
  }>;
  model: string;
  usage: { prompt_tokens: number; completion_tokens: number };
}

function parseOpenAIChatResponse(value: unknown): OpenAIChatResponse {
  if (!isRecord(value) || !Array.isArray(value["choices"])) {
    throw new ProviderError("OpenAI-compatible API returned a malformed chat response");
  }
  const usage = value["usage"];
  if (
    typeof value["model"] !== "string" ||
    !isRecord(usage) ||
    typeof usage["prompt_tokens"] !== "number" ||
    typeof usage["completion_tokens"] !== "number"
  ) {
    throw new ProviderError("OpenAI-compatible API returned malformed model or usage metadata");
  }
  const choices = value["choices"].map((rawChoice) => {
    if (!isRecord(rawChoice) || !isRecord(rawChoice["message"])) {
      throw new ProviderError("OpenAI-compatible API returned a malformed completion choice");
    }
    const rawMessage = rawChoice["message"];
    let toolCalls: OpenAIToolCall[] | undefined;
    if (Array.isArray(rawMessage["tool_calls"])) {
      toolCalls = rawMessage["tool_calls"].map((rawToolCall): OpenAIToolCall => {
        if (!isRecord(rawToolCall)) {
          throw new ProviderError("OpenAI-compatible API returned a malformed tool call");
        }
        const rawFunction = rawToolCall["function"];
        const toolCall: OpenAIToolCall = {};
        if (typeof rawToolCall["id"] === "string") toolCall.id = rawToolCall["id"];
        if (typeof rawToolCall["type"] === "string") toolCall.type = rawToolCall["type"];
        if (isRecord(rawFunction)) {
          toolCall.function = {};
          if (typeof rawFunction["name"] === "string") {
            toolCall.function.name = rawFunction["name"];
          }
          if ("arguments" in rawFunction) toolCall.function.arguments = rawFunction["arguments"];
        }
        return toolCall;
      });
    }
    return {
      message: {
        content: typeof rawMessage["content"] === "string" ? rawMessage["content"] : null,
        ...(toolCalls ? { tool_calls: toolCalls } : {}),
      },
      ...(typeof rawChoice["finish_reason"] === "string"
        ? { finish_reason: rawChoice["finish_reason"] }
        : {}),
    };
  });
  return {
    choices,
    model: value["model"],
    usage: {
      prompt_tokens: usage["prompt_tokens"],
      completion_tokens: usage["completion_tokens"],
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
  runtimeSession?: RuntimeSession;
  runtimeSessionRole?: string;
  runtimeSessionCwd?: string;
  runtimeSessionCommands?: RuntimeCommandGrant[];
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
    // Naming collision only: this is an OpenAI-compatible gateway pointed at a
    // Hermes-3-Llama model. Unrelated to autocontext.hermes (Python), which
    // integrates with NousResearch's Hermes agent Curator subsystem.
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
    return createRuntimeBridgeProvider(runtime, resolvedModel ?? "sonnet", opts, "claude-cli");
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
    return createRuntimeBridgeProvider(runtime, resolvedModel ?? "o4-mini", opts, "codex");
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
    return createRuntimeBridgeProvider(runtime, resolvedModel ?? "pi-default", opts, "pi");
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
    return createRuntimeBridgeProvider(runtime, resolvedModel ?? "pi-rpc-default", opts, "pi-rpc");
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

function createRuntimeBridgeProvider(
  runtime: AgentRuntime,
  model: string,
  opts: CreateProviderOpts,
  defaultRole: string,
): LLMProvider {
  return new RuntimeBridgeProvider(runtime, model, runtimeBridgeProviderOpts(opts, defaultRole));
}

function runtimeBridgeProviderOpts(
  opts: CreateProviderOpts,
  defaultRole: string,
): RuntimeBridgeProviderOpts {
  if (!opts.runtimeSession) return {};
  return {
    session: opts.runtimeSession,
    role: opts.runtimeSessionRole ?? defaultRole,
    cwd: opts.runtimeSessionCwd,
    commands: opts.runtimeSessionCommands,
  };
}
