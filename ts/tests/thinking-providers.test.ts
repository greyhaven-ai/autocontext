import { afterEach, describe, expect, it, vi } from "vitest";

import { RetryProvider } from "../src/agents/provider-bridge.js";
import {
  completeWithThinkingFallback,
  createAnthropicProvider,
  createOpenAICompatibleProvider,
} from "../src/providers/index.js";
import type { LLMProvider } from "../src/types/index.js";
import { ProviderError, ThinkingUnsupportedError } from "../src/types/index.js";

afterEach(() => vi.unstubAllGlobals());

function okJson(payload: unknown) {
  return { ok: true, json: async () => payload };
}

function openAIToolResponse(thoughts: string, input = 3, output = 2) {
  return okJson({
    choices: [
      {
        message: {
          content: null,
          tool_calls: [
            {
              id: "call-1",
              type: "function",
              function: { name: "deep_think", arguments: JSON.stringify({ thoughts }) },
            },
          ],
        },
        finish_reason: "tool_calls",
      },
    ],
    model: "gpt-stub",
    usage: { prompt_tokens: input, completion_tokens: output },
  });
}

function openAIFinalResponse(text: string, input = 3, output = 2) {
  return okJson({
    choices: [{ message: { content: text }, finish_reason: "stop" }],
    model: "gpt-stub",
    usage: { prompt_tokens: input, completion_tokens: output },
  });
}

describe("thinking provider contract", () => {
  it("reports an honest unsupported fallback for legacy providers", async () => {
    const provider: LLMProvider = {
      name: "legacy",
      defaultModel: () => "legacy",
      complete: async () => ({ text: "visible only", model: "legacy", usage: {} }),
    };

    const result = await completeWithThinkingFallback(provider, {
      systemPrompt: "system",
      userPrompt: "user",
    });

    expect(result.thinkingStream).toEqual([]);
    expect(result.thinkingCapture).toBe("unsupported");
  });

  it("falls back when a nominally native endpoint rejects thinking tools", async () => {
    const provider: LLMProvider = {
      name: "dynamic-capability",
      supportsThinkingStream: true,
      defaultModel: () => "dynamic-capability",
      complete: async () => ({ text: "visible only", usage: { input: 11, output: 13 } }),
      completeWithThinking: async () => {
        throw new ThinkingUnsupportedError("tools unsupported", { input: 5, output: 7 });
      },
    };

    const result = await completeWithThinkingFallback(provider, {
      systemPrompt: "system",
      userPrompt: "user",
    });

    expect(result.text).toBe("visible only");
    expect(result.thinkingStream).toEqual([]);
    expect(result.thinkingCapture).toBe("unsupported");
    expect(result.usage).toEqual({ input: 16, output: 20 });
  });

  it("does not convert unrelated provider failures into ordinary completions", async () => {
    const complete = vi.fn(async () => ({ text: "must not run", usage: {} }));
    const provider: LLMProvider = {
      name: "broken-native",
      supportsThinkingStream: true,
      defaultModel: () => "broken-native",
      complete,
      completeWithThinking: async () => {
        throw new ProviderError("connection reset");
      },
    };

    await expect(
      completeWithThinkingFallback(provider, { systemPrompt: "system", userPrompt: "user" }),
    ).rejects.toThrow("connection reset");
    expect(complete).not.toHaveBeenCalled();
  });

  it("retries the whole native thinking operation through RetryProvider", async () => {
    let attempts = 0;
    const inner: LLMProvider = {
      name: "native",
      supportsThinkingStream: true,
      defaultModel: () => "native",
      complete: async () => ({ text: "unused", model: "native", usage: {} }),
      completeWithThinking: async () => {
        attempts += 1;
        if (attempts === 1) throw new Error("temporary");
        return {
          text: "final",
          thinkingStream: ["captured"],
          thinkingCapture: "tool",
          usage: {},
        };
      },
    };
    const provider = new RetryProvider(inner, { maxRetries: 1, baseDelay: 0 });

    const result = await provider.completeWithThinking({ systemPrompt: "s", userPrompt: "u" });

    expect(provider.supportsThinkingStream).toBe(true);
    expect(attempts).toBe(2);
    expect(result.thinkingStream).toEqual(["captured"]);
  });

  it("preserves partial usage when retrying a thinking operation", async () => {
    let attempts = 0;
    const inner: LLMProvider = {
      name: "native",
      supportsThinkingStream: true,
      defaultModel: () => "native",
      complete: async () => ({ text: "unused", usage: {} }),
      completeWithThinking: async () => {
        attempts += 1;
        if (attempts === 1) {
          throw new ProviderError("temporary", { input: 5, output: 7 });
        }
        return {
          text: "final",
          thinkingStream: ["captured"],
          thinkingCapture: "tool",
          usage: { input: 11, output: 13 },
        };
      },
    };
    const provider = new RetryProvider(inner, { maxRetries: 1, baseDelay: 0 });

    const result = await provider.completeWithThinking({ systemPrompt: "s", userPrompt: "u" });

    expect(result.usage).toEqual({ input: 16, output: 20 });
  });
});

describe("OpenAI-compatible deep_think", () => {
  it("captures ordered tool payloads separately from final text", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce(openAIToolResponse("check invariant", 5, 7))
      .mockResolvedValueOnce(openAIFinalResponse('{"answer":"done"}', 11, 13));
    vi.stubGlobal("fetch", mockFetch);
    const provider = createOpenAICompatibleProvider({ apiKey: "test" });
    expect(provider.completeWithThinking).toBeDefined();

    const result = await provider.completeWithThinking!({ systemPrompt: "s", userPrompt: "u" });

    expect(result.text).toBe('{"answer":"done"}');
    expect(result.thinkingStream).toEqual(["check invariant"]);
    expect(result.thinkingTool).toBe("deep_think");
    expect(result.thinkingCapture).toBe("tool");
    expect(result.usage).toEqual({ input: 16, output: 20 });
    expect(provider.supportsThinkingStream).toBe(true);

    const firstBody = JSON.parse(mockFetch.mock.calls[0][1].body);
    const secondBody = JSON.parse(mockFetch.mock.calls[1][1].body);
    expect(firstBody.tool_choice).toBe("required");
    expect(secondBody.tool_choice).toBe("auto");
    expect(firstBody.parallel_tool_calls).toBe(false);
    expect(firstBody.reasoning_effort).toBe("none");
    expect(firstBody.tools[0].function.name).toBe("deep_think");
    expect(firstBody.tools[0].function.strict).toBe(true);
    expect(secondBody.messages.at(-1).role).toBe("tool");
    expect(secondBody.messages.at(-1).content).not.toContain("check invariant");
    expect(secondBody.messages.at(-1).content).toBe('{"recorded":1}');
  });

  it("negotiates away only an unsupported reasoning_effort field", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 400,
        text: async () => "unknown parameter reasoning_effort",
      })
      .mockResolvedValueOnce(openAIToolResponse("portable tool"))
      .mockResolvedValueOnce(openAIFinalResponse("final"));
    vi.stubGlobal("fetch", mockFetch);
    const provider = createOpenAICompatibleProvider({ apiKey: "test" });

    const result = await provider.completeWithThinking!({ systemPrompt: "s", userPrompt: "u" });

    expect(result.thinkingStream).toEqual(["portable tool"]);
    expect(JSON.parse(mockFetch.mock.calls[0][1].body).reasoning_effort).toBe("none");
    expect(JSON.parse(mockFetch.mock.calls[1][1].body).reasoning_effort).toBeUndefined();
    expect(JSON.parse(mockFetch.mock.calls[2][1].body).reasoning_effort).toBeUndefined();
  });

  it("uses the lowest advertised gateway level when none is rejected", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 400,
        text: async () => 'level "none" not supported, valid levels: low, medium, high',
      })
      .mockResolvedValueOnce(openAIToolResponse("portable scratchpad"))
      .mockResolvedValueOnce(openAIFinalResponse("final"));
    vi.stubGlobal("fetch", mockFetch);
    const provider = createOpenAICompatibleProvider({ apiKey: "test" });

    const result = await provider.completeWithThinking!({
      systemPrompt: "s",
      userPrompt: "u",
      reasoningEffort: "high",
    });

    expect(result.thinkingStream).toEqual(["portable scratchpad"]);
    expect(JSON.parse(mockFetch.mock.calls[0][1].body).reasoning_effort).toBe("none");
    expect(JSON.parse(mockFetch.mock.calls[1][1].body).reasoning_effort).toBe("low");
    expect(JSON.parse(mockFetch.mock.calls[2][1].body).reasoning_effort).toBe("low");
  });

  it("negotiates the strict flag while retaining local argument validation", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 400,
        text: async () => "unknown field tools[0].function.strict",
      })
      .mockResolvedValueOnce(openAIToolResponse("portable schema"))
      .mockResolvedValueOnce(openAIFinalResponse("final"));
    vi.stubGlobal("fetch", mockFetch);
    const provider = createOpenAICompatibleProvider({ apiKey: "test" });

    const result = await provider.completeWithThinking!({ systemPrompt: "s", userPrompt: "u" });

    expect(result.thinkingStream).toEqual(["portable schema"]);
    expect(JSON.parse(mockFetch.mock.calls[0][1].body).tools[0].function.strict).toBe(true);
    expect(JSON.parse(mockFetch.mock.calls[1][1].body).tools[0].function.strict).toBeUndefined();
    expect(JSON.parse(mockFetch.mock.calls[2][1].body).tools[0].function.strict).toBeUndefined();
  });

  it("maps GPT 5.6 external effort to the numeric prompt budget", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce(openAIToolResponse("budgeted scratchpad"))
      .mockResolvedValueOnce(openAIFinalResponse("final"));
    vi.stubGlobal("fetch", mockFetch);
    const provider = createOpenAICompatibleProvider({
      apiKey: "test",
      model: "openai-codex/gpt-5.6-sol",
    });

    await provider.completeWithThinking!({
      systemPrompt: "s",
      userPrompt: "u",
      reasoningEffort: "high",
    });

    const firstBody = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(firstBody.reasoning_effort).toBe("none");
    expect(firstBody.messages[0].content).toMatch(/# Juice: 48 !important$/);
  });

  it("fails closed on malformed tool arguments", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        okJson({
          choices: [
            {
              message: {
                content: null,
                tool_calls: [
                  {
                    id: "call-invalid",
                    type: "function",
                    function: { name: "deep_think", arguments: "not-json" },
                  },
                ],
              },
              finish_reason: "tool_calls",
            },
          ],
          model: "gpt-stub",
          usage: { prompt_tokens: 1, completion_tokens: 1 },
        }),
      ),
    );
    const provider = createOpenAICompatibleProvider({ apiKey: "test" });

    await expect(
      provider.completeWithThinking!({ systemPrompt: "s", userPrompt: "u" }),
    ).rejects.toThrow("Invalid deep_think arguments");
  });

  it("fails closed when the required first tool call is ignored", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(openAIFinalResponse("uncaptured answer")));
    const provider = createOpenAICompatibleProvider({ apiKey: "test" });

    await expect(
      provider.completeWithThinking!({ systemPrompt: "s", userPrompt: "u" }),
    ).rejects.toThrow("did not honor required deep_think");
  });

  it("reports endpoint-level tool rejection as unsupported", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        text: async () => "tools are not supported",
      }),
    );
    const provider = createOpenAICompatibleProvider({ apiKey: "test" });

    await expect(
      provider.completeWithThinking!({ systemPrompt: "s", userPrompt: "u" }),
    ).rejects.toBeInstanceOf(ThinkingUnsupportedError);
  });

  it("fails closed at the configured tool-turn bound", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(openAIToolResponse("still thinking")));
    const provider = createOpenAICompatibleProvider({ apiKey: "test" });

    await expect(
      provider.completeWithThinking!({ systemPrompt: "s", userPrompt: "u", maxToolTurns: 2 }),
    ).rejects.toThrow("exceeded 2 deep_think tool turns");
  });

  it("allows a final answer after the last permitted tool turn", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce(openAIToolResponse("bounded thought"))
      .mockResolvedValueOnce(openAIFinalResponse("final"));
    vi.stubGlobal("fetch", mockFetch);
    const provider = createOpenAICompatibleProvider({ apiKey: "test" });

    const result = await provider.completeWithThinking!({
      systemPrompt: "s",
      userPrompt: "u",
      maxToolTurns: 1,
    });

    expect(result.text).toBe("final");
    expect(result.thinkingStream).toEqual(["bounded thought"]);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });
});

describe("Anthropic deep_think", () => {
  it("uses native client-tool blocks and returns a separate stream", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce(
        okJson({
          content: [
            {
              type: "tool_use",
              id: "toolu-1",
              name: "deep_think",
              input: { thoughts: "establish invariant" },
            },
          ],
          model: "claude-stub",
          usage: { input_tokens: 5, output_tokens: 7 },
          stop_reason: "tool_use",
        }),
      )
      .mockResolvedValueOnce(
        okJson({
          content: [{ type: "text", text: "final" }],
          model: "claude-stub",
          usage: { input_tokens: 11, output_tokens: 13 },
          stop_reason: "end_turn",
        }),
      );
    vi.stubGlobal("fetch", mockFetch);
    const provider = createAnthropicProvider({ apiKey: "test" });

    const result = await provider.completeWithThinking!({ systemPrompt: "s", userPrompt: "u" });

    expect(result.text).toBe("final");
    expect(result.thinkingStream).toEqual(["establish invariant"]);
    expect(result.thinkingCapture).toBe("tool");
    expect(result.usage).toEqual({ input: 16, output: 20 });
    expect(provider.supportsThinkingStream).toBe(true);

    const firstBody = JSON.parse(mockFetch.mock.calls[0][1].body);
    const secondBody = JSON.parse(mockFetch.mock.calls[1][1].body);
    expect(firstBody.tool_choice).toEqual({
      type: "tool",
      name: "deep_think",
      disable_parallel_tool_use: true,
    });
    expect(secondBody.tool_choice).toEqual({ type: "auto", disable_parallel_tool_use: true });
    expect(secondBody.messages.at(-1).content[0].type).toBe("tool_result");
    expect(secondBody.messages.at(-1).content[0].content).not.toContain("establish invariant");
    expect(secondBody.messages.at(-1).content[0].content).toBe('{"recorded":1}');
  });

  it("fails closed on malformed tool input", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        okJson({
          content: [
            {
              type: "tool_use",
              id: "toolu-invalid",
              name: "deep_think",
              input: { unexpected: "shape" },
            },
          ],
          model: "claude-stub",
          usage: { input_tokens: 1, output_tokens: 1 },
          stop_reason: "tool_use",
        }),
      ),
    );
    const provider = createAnthropicProvider({ apiKey: "test" });

    await expect(
      provider.completeWithThinking!({ systemPrompt: "s", userPrompt: "u" }),
    ).rejects.toThrow("Invalid deep_think arguments");
  });

  it("fails closed when the required first tool call is ignored", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        okJson({
          content: [{ type: "text", text: "uncaptured answer" }],
          model: "claude-stub",
          usage: { input_tokens: 1, output_tokens: 1 },
          stop_reason: "end_turn",
        }),
      ),
    );
    const provider = createAnthropicProvider({ apiKey: "test" });

    await expect(
      provider.completeWithThinking!({ systemPrompt: "s", userPrompt: "u" }),
    ).rejects.toThrow("did not honor required deep_think");
  });

  it("fails closed at the configured tool-turn bound", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        okJson({
          content: [
            {
              type: "tool_use",
              id: "toolu-repeat",
              name: "deep_think",
              input: { thoughts: "still thinking" },
            },
          ],
          model: "claude-stub",
          usage: { input_tokens: 1, output_tokens: 1 },
          stop_reason: "tool_use",
        }),
      ),
    );
    const provider = createAnthropicProvider({ apiKey: "test" });

    await expect(
      provider.completeWithThinking!({ systemPrompt: "s", userPrompt: "u", maxToolTurns: 2 }),
    ).rejects.toThrow("exceeded 2 deep_think tool turns");
  });

  it("allows a final answer after the last permitted tool turn", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce(
        okJson({
          content: [
            {
              type: "tool_use",
              id: "toolu-bounded",
              name: "deep_think",
              input: { thoughts: "bounded thought" },
            },
          ],
          model: "claude-stub",
          usage: { input_tokens: 3, output_tokens: 2 },
          stop_reason: "tool_use",
        }),
      )
      .mockResolvedValueOnce(
        okJson({
          content: [{ type: "text", text: "final" }],
          model: "claude-stub",
          usage: { input_tokens: 3, output_tokens: 2 },
          stop_reason: "end_turn",
        }),
      );
    vi.stubGlobal("fetch", mockFetch);
    const provider = createAnthropicProvider({ apiKey: "test" });

    const result = await provider.completeWithThinking!({
      systemPrompt: "s",
      userPrompt: "u",
      maxToolTurns: 1,
    });

    expect(result.text).toBe("final");
    expect(result.thinkingStream).toEqual(["bounded thought"]);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });
});
