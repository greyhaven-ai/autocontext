import { ProviderError, ThinkingUnsupportedError } from "../types/index.js";
import { clampOutputTokens } from "./token-caps.js";
import type {
  CompletionOptions,
  CompletionResult,
  LLMProvider,
  ValidatedImageAttachment,
} from "../types/index.js";
import { encodeValidatedImage } from "../types/index.js";
import { DeterministicProvider } from "./deterministic.js";
import {
  DEEP_THINK_DESCRIPTION,
  DEEP_THINK_PARAMETERS,
  DEEP_THINK_TOOL_NAME,
  addCompletionUsage,
  deepThinkJuice,
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
} from "./runtime-bridge.js";
import type { AgentRuntime } from "../runtimes/base.js";
import { SUPPORTED_PROVIDER_TYPES } from "./supported-provider-types.js";
import type { RuntimeCommandGrant } from "../runtimes/workspace-env.js";
import type { RuntimeSession } from "../session/runtime-session.js";

export { SUPPORTED_PROVIDER_TYPES } from "./supported-provider-types.js";

export interface AnthropicProviderOpts {
  apiKey: string;
  model?: string;
}

function supportsAnthropicImages(model: string): boolean {
  return /(?:^|\/)claude-(?:3|haiku|sonnet|opus|fable|mythos)(?:$|[-:.])/i.test(model);
}

function assertImagePath(
  provider: string,
  model: string,
  supported: boolean,
  attachments: readonly ValidatedImageAttachment[] | undefined,
): void {
  if (attachments?.length && !supported) {
    throw new ProviderError(
      `Provider '${provider}' model '${model}' does not support image attachments`,
    );
  }
}

function anthropicUserContent(opts: CompletionOptions): string | Array<Record<string, unknown>> {
  if (!opts.imageAttachments?.length) return opts.userPrompt;
  return [
    ...opts.imageAttachments.map((attachment) => ({
      type: "image",
      source: {
        type: "base64",
        media_type: attachment.mediaType,
        data: encodeValidatedImage(attachment),
      },
    })),
    { type: "text", text: opts.userPrompt },
  ];
}

function claudeRequestControls(model: string, temperature: number): Record<string, unknown> {
  const isClaude5 = /(?:^|\/)claude-(?:sonnet|opus|fable|mythos)-5(?:$|[-:])/i.test(model);
  if (!isClaude5) return { temperature };

  const canDisableThinking = /(?:^|\/)claude-(?:sonnet|opus)-5(?:$|[-:])/i.test(model);
  return canDisableThinking ? { thinking: { type: "disabled" } } : {};
}

export function createAnthropicProvider(opts: AnthropicProviderOpts): LLMProvider {
  const defaultModel = opts.model || "claude-sonnet-5";
  const supportsImages = (model = defaultModel) => supportsAnthropicImages(model);

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
    supportsImageAttachments: supportsImages,
    defaultModel: () => defaultModel,
    complete: async (callOpts) => {
      const model = callOpts.model || defaultModel;
      assertImagePath("anthropic", model, supportsImages(model), callOpts.imageAttachments);
      const data = await post({
        model,
        max_tokens: clampOutputTokens(callOpts.maxTokens ?? 4096, model),
        system: callOpts.systemPrompt,
        messages: [{ role: "user", content: anthropicUserContent(callOpts) }],
        ...claudeRequestControls(model, callOpts.temperature ?? 0),
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
      assertImagePath("anthropic", model, supportsImages(model), callOpts.imageAttachments);
      const messages: Array<Record<string, unknown>> = [
        { role: "user", content: anthropicUserContent(callOpts) },
      ];
      const thinkingStream: string[] = [];
      const usage: Record<string, number> = {};

      // maxToolTurns bounds tool-bearing responses. The final permitted tool
      // turn still needs one subsequent request for the answer.
      for (let turn = 0; turn <= maxToolTurns; turn++) {
        const toolChoice =
          turn === 0
            ? { type: "tool", name: DEEP_THINK_TOOL_NAME, disable_parallel_tool_use: true }
            : { type: "auto", disable_parallel_tool_use: true };
        let data: AnthropicMessageResponse;
        try {
          data = await post({
            model,
            max_tokens: clampOutputTokens(callOpts.maxTokens ?? 4096, model),
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
            ...claudeRequestControls(model, callOpts.temperature ?? 0),
          });
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          throw new ProviderError(message, usage);
        }
        addCompletionUsage(usage, {
          input: data.usage.input_tokens,
          output: data.usage.output_tokens,
        });

        const toolBlocks = data.content.filter((block) => block.type === "tool_use");
        if (toolBlocks.length === 0) {
          if (turn === 0) {
            throw new ThinkingUnsupportedError(
              "Anthropic API did not honor required deep_think tool choice",
              usage,
            );
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

        if (turn === maxToolTurns) {
          throw new ProviderError(`Model exceeded ${maxToolTurns} deep_think tool turns`, usage);
        }

        messages.push({ role: "assistant", content: data.content });
        const toolResults: Array<Record<string, unknown>> = [];
        for (const block of toolBlocks) {
          if (block.name !== DEEP_THINK_TOOL_NAME) {
            throw new ProviderError(
              `Unexpected thinking tool call: ${block.name || "<missing>"}`,
              usage,
            );
          }
          try {
            thinkingStream.push(extractDeepThought(block.input));
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            throw new ProviderError(message, usage);
          }
          toolResults.push({
            type: "tool_result",
            tool_use_id: block.id ?? "",
            content: deepThinkAcknowledgement(thinkingStream.length),
          });
        }
        messages.push({ role: "user", content: toolResults });
      }
      throw new Error("deep_think loop exhausted without returning or raising");
    },
  };
}

interface AnthropicContentBlock {
  [key: string]: unknown;
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
    // Keep thinking/signature fields intact for adaptive-thinking models: the
    // Messages API requires them to be round-tripped unchanged with tool use.
    const block: AnthropicContentBlock = { ...raw, type: raw["type"] };
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
  /** Explicit opt-in for compatible gateways whose multimodal contract is known. */
  imageSupport?: boolean | ((model: string) => boolean);
}

function isOfficialOpenAIBaseUrl(baseUrl: string): boolean {
  try {
    const parsed = new URL(baseUrl);
    return parsed.protocol === "https:" && parsed.hostname === "api.openai.com";
  } catch {
    return false;
  }
}

function supportsOpenAIImages(model: string): boolean {
  return /(?:^|\/)(?:chatgpt-4o|gpt-(?:4o|4\.1|4-vision|5(?:[.-]\d+)*)(?:$|[-:.])|o[134](?:-|$))/i.test(model);
}

function openAIUserContent(opts: CompletionOptions): string | Array<Record<string, unknown>> {
  if (!opts.imageAttachments?.length) return opts.userPrompt;
  return [
    { type: "text", text: opts.userPrompt },
    ...opts.imageAttachments.map((attachment) => ({
      type: "image_url",
      image_url: {
        url: `data:${attachment.mediaType};base64,${encodeValidatedImage(attachment)}`,
      },
    })),
  ];
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
    message.includes("reasoning_effort") ||
    message.includes("reasoning effort") ||
    /(?:valid|supported|allowed) levels?/.test(message);
  const rejectsField = [
    "unsupported",
    "not supported",
    "unknown",
    "unrecognized",
    "invalid",
  ].some((token) => message.includes(token));
  return mentionsField && rejectsField;
}

function isUnsupportedMaxCompletionTokensError(status: number, body: string): boolean {
  if (![400, 404, 422].includes(status)) return false;
  const message = body.toLowerCase();
  return (
    message.includes("max_completion_tokens") &&
    ["unsupported", "not supported", "unknown", "unrecognized", "unexpected", "invalid"].some(
      (token) => message.includes(token),
    )
  );
}

function isUnsupportedStrictToolsError(status: number, body: string): boolean {
  if (![400, 404, 422].includes(status)) return false;
  const message = body.toLowerCase();
  const mentionsField =
    message.includes("strict") && (message.includes("tool") || message.includes("function"));
  const rejectsField = [
    "unsupported",
    "not supported",
    "unknown",
    "unrecognized",
    "unexpected",
    "invalid",
  ].some((token) => message.includes(token));
  return mentionsField && rejectsField;
}

function isUnsupportedToolsError(status: number, body: string): boolean {
  if (![400, 404, 422].includes(status)) return false;
  const message = body.toLowerCase();
  const mentionsTools = [
    "tools",
    "tool_choice",
    "tool choice",
    "function calling",
    "function_call",
  ].some((token) => message.includes(token));
  const rejectsTools = [
    "unsupported",
    "not supported",
    "unknown",
    "unrecognized",
    "invalid",
  ].some((token) => message.includes(token));
  return mentionsTools && rejectsTools;
}

const REASONING_EFFORT_ORDER = [
  "none",
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
] as const;

function lowestSupportedReasoningEffort(body: string, current: string): string | undefined {
  const message = body.toLowerCase();
  if (!/(?:valid|supported|allowed) levels?/.test(message)) return undefined;
  const advertised = new Set(message.match(/\b(?:none|minimal|low|medium|high|xhigh|max)\b/g) ?? []);
  advertised.delete(current.toLowerCase());
  return REASONING_EFFORT_ORDER.find((effort) => advertised.has(effort));
}

function isGpt56Plus(model: string): boolean {
  const match = /(?:^|[/:-])gpt-(\d+)(?:\.(\d+))?/i.exec(model);
  if (!match) return false;
  const major = Number(match[1]);
  const minor = Number(match[2] ?? 0);
  return major > 5 || (major === 5 && minor >= 6);
}

function supportsTemperature(model: string): boolean {
  return !/(?:^|\/)gemini-3\.(?:5|6)(?:$|[-:])/i.test(model);
}

function outputTokenField(model: string): "max_completion_tokens" | "max_tokens" {
  return isGpt56Plus(model) ? "max_completion_tokens" : "max_tokens";
}

export function createOpenAICompatibleProvider(opts: OpenAICompatibleProviderOpts): LLMProvider {
  const defaultModel = opts.model || "gpt-5.6-terra";
  const baseUrl = (opts.baseUrl ?? "https://api.openai.com/v1").replace(/\/+$/, "");
  const apiKey = opts.apiKey ?? "";
  const supportsImages = (model = defaultModel) =>
    typeof opts.imageSupport === "function"
      ? opts.imageSupport(model)
      : opts.imageSupport ?? (isOfficialOpenAIBaseUrl(baseUrl) && supportsOpenAIImages(model));

  return {
    name: "openai-compatible",
    supportsThinkingStream: true,
    supportsImageAttachments: supportsImages,
    defaultModel: () => defaultModel,
    complete: async (callOpts) => {
      const model = callOpts.model || defaultModel;
      assertImagePath("openai-compatible", model, supportsImages(model), callOpts.imageAttachments);
      let wireReasoningEffort: string | undefined = isGpt56Plus(model) ? "none" : undefined;
      let tokenField = outputTokenField(model);
      const buildBody = (withSchema: boolean) =>
        JSON.stringify({
          model,
          [tokenField]: clampOutputTokens(callOpts.maxTokens ?? 4096, model),
          ...(supportsTemperature(model) ? { temperature: callOpts.temperature ?? 0 } : {}),
          ...(wireReasoningEffort !== undefined
            ? { reasoning_effort: wireReasoningEffort }
            : {}),
          messages: [
            { role: "system", content: callOpts.systemPrompt },
            { role: "user", content: openAIUserContent(callOpts) },
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
      let res: Response;
      while (true) {
        res = await post(constrained);
        if (res.ok) break;

        const body = await res.text();
        if (constrained && isUnsupportedResponseFormatError(res.status, body)) {
          // This endpoint explicitly rejected response_format. Retry without
          // it and report that the returned text was unconstrained.
          constrained = false;
          continue;
        }
        if (
          wireReasoningEffort !== undefined &&
          isUnsupportedReasoningEffortError(res.status, body)
        ) {
          // Compatible gateways may use a GPT-like id without exposing the
          // native control. Preserve portability after first trying to avoid
          // GPT-5.6's implicit medium reasoning.
          wireReasoningEffort = undefined;
          continue;
        }
        if (
          tokenField === "max_completion_tokens" &&
          isUnsupportedMaxCompletionTokensError(res.status, body)
        ) {
          tokenField = "max_tokens";
          continue;
        }
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
      assertImagePath("openai-compatible", model, supportsImages(model), callOpts.imageAttachments);
      const juice = isGpt56Plus(model)
        ? deepThinkJuice(callOpts.reasoningEffort ?? "medium")
        : undefined;
      const messages: Array<Record<string, unknown>> = [
        { role: "system", content: withDeepThinkInstruction(callOpts.systemPrompt, juice) },
        { role: "user", content: openAIUserContent(callOpts) },
      ];
      const thinkingStream: string[] = [];
      const usage: Record<string, number> = {};
      let constrained = Boolean(callOpts.outputSchema);
      // reasoningEffort controls the external prompt budget. Keep native
      // hidden reasoning off so the explicit tool payload is what callers
      // collect; gateways that reject `none` may advertise a lowest level.
      let wireReasoningEffort: string | undefined = "none";
      let strictTools = true;

      // maxToolTurns bounds tool-bearing responses, with one extra request
      // available for the model to return its final answer.
      for (let turn = 0; turn <= maxToolTurns; turn++) {
        const body: Record<string, unknown> = {
          model,
          [outputTokenField(model)]: clampOutputTokens(callOpts.maxTokens ?? 4096, model),
          messages,
          tools: [
            {
              type: "function",
              function: {
                name: DEEP_THINK_TOOL_NAME,
                description: DEEP_THINK_DESCRIPTION,
                parameters: DEEP_THINK_PARAMETERS,
                ...(strictTools ? { strict: true } : {}),
              },
            },
          ],
          tool_choice: turn === 0 ? "required" : "auto",
          parallel_tool_calls: false,
        };
        if (supportsTemperature(model)) {
          body["temperature"] = callOpts.temperature ?? 0;
        }
        if (wireReasoningEffort !== undefined) {
          body["reasoning_effort"] = wireReasoningEffort;
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
          let res: Response;
          try {
            res = await fetch(`${baseUrl}/chat/completions`, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${apiKey}`,
              },
              body: JSON.stringify(body),
            });
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            throw new ProviderError(`OpenAI API error: ${message}`, usage);
          }
          if (res.ok) {
            try {
              data = parseOpenAIChatResponse(await res.json());
            } catch (error) {
              const message = error instanceof Error ? error.message : String(error);
              throw new ProviderError(message, usage);
            }
            break;
          }

          const errorBody = await res.text();
          if (constrained && isUnsupportedResponseFormatError(res.status, errorBody)) {
            constrained = false;
            delete body["response_format"];
            continue;
          }
          if (strictTools && isUnsupportedStrictToolsError(res.status, errorBody)) {
            strictTools = false;
            const tools = body["tools"];
            const firstTool = Array.isArray(tools) ? tools[0] : undefined;
            const toolFunction = isRecord(firstTool) ? firstTool["function"] : undefined;
            if (isRecord(toolFunction)) delete toolFunction["strict"];
            continue;
          }
          if (
            "reasoning_effort" in body &&
            isUnsupportedReasoningEffortError(res.status, errorBody)
          ) {
            const currentEffort = String(body["reasoning_effort"]);
            const fallbackEffort = lowestSupportedReasoningEffort(errorBody, currentEffort);
            if (fallbackEffort === undefined) {
              wireReasoningEffort = undefined;
              delete body["reasoning_effort"];
            } else {
              wireReasoningEffort = fallbackEffort;
              body["reasoning_effort"] = fallbackEffort;
            }
            continue;
          }
          if (
            "max_completion_tokens" in body &&
            isUnsupportedMaxCompletionTokensError(res.status, errorBody)
          ) {
            body["max_tokens"] = body["max_completion_tokens"];
            delete body["max_completion_tokens"];
            continue;
          }
          if (isUnsupportedToolsError(res.status, errorBody)) {
            throw new ThinkingUnsupportedError(
              `OpenAI-compatible endpoint does not support thinking tools: ${errorBody.slice(0, 200)}`,
              usage,
            );
          }
          throw new ProviderError(
            `OpenAI API error ${res.status}: ${errorBody.slice(0, 200)}`,
            usage,
          );
        }

        addCompletionUsage(usage, {
          input: data.usage.prompt_tokens,
          output: data.usage.completion_tokens,
        });
        const choice = data.choices[0];
        if (!choice) {
          throw new ProviderError("OpenAI-compatible API returned no completion choices", usage);
        }
        const toolCalls = choice.message.tool_calls ?? [];
        if (toolCalls.length === 0) {
          if (turn === 0) {
            throw new ThinkingUnsupportedError(
              "OpenAI-compatible endpoint did not honor required deep_think tool choice",
              usage,
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

        if (turn === maxToolTurns) {
          throw new ProviderError(`Model exceeded ${maxToolTurns} deep_think tool turns`, usage);
        }

        messages.push({
          role: "assistant",
          content: choice.message.content ?? null,
          tool_calls: toolCalls,
        });
        for (const toolCall of toolCalls) {
          const name = toolCall.function?.name ?? "";
          if (name !== DEEP_THINK_TOOL_NAME) {
            throw new ProviderError(
              `Unexpected thinking tool call: ${name || "<missing>"}`,
              usage,
            );
          }
          try {
            thinkingStream.push(extractDeepThought(toolCall.function?.arguments));
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            throw new ProviderError(message, usage);
          }
          messages.push({
            role: "tool",
            tool_call_id: toolCall.id ?? "",
            content: deepThinkAcknowledgement(thinkingStream.length),
          });
        }
      }
      throw new Error("deep_think loop exhausted without returning or raising");
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
    defaultModel: "gemini-3.1-pro-preview",
  },
  mistral: {
    baseUrl: "https://api.mistral.ai/v1",
    envVar: "MISTRAL_API_KEY",
    defaultModel: "mistral-large-2512",
  },
  groq: {
    baseUrl: "https://api.groq.com/openai/v1",
    envVar: "GROQ_API_KEY",
    defaultModel: "llama-3.3-70b-versatile",
  },
  openrouter: {
    baseUrl: "https://openrouter.ai/api/v1",
    envVar: "OPENROUTER_API_KEY",
    defaultModel: "anthropic/claude-sonnet-5",
  },
  "azure-openai": {
    envVar: "AZURE_OPENAI_API_KEY",
    defaultModel: "gpt-5.6-terra",
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
