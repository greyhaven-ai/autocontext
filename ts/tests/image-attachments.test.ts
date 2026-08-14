import { createHash } from "node:crypto";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  IMAGE_ATTACHMENTS_CAPABILITY,
  parseClientMessage,
} from "../src/server/protocol.js";
import { executeChatAgentCommand } from "../src/server/chat-agent-command-workflow.js";
import { buildHelloMessage } from "../src/server/websocket-session-bootstrap.js";
import {
  createAnthropicProvider,
  createOpenAICompatibleProvider,
} from "../src/providers/provider-factory.js";
import {
  ImageAttachmentValidationError,
  MAX_IMAGE_SOURCE_BYTES,
  validateImageAttachments,
  type ImageAttachment,
} from "../src/types/image-attachments.js";

const ONE_BY_ONE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZsOkAAAAASUVORK5CYII=",
  "base64",
);

function attachment(
  overrides: Partial<ImageAttachment> = {},
  bytes = ONE_BY_ONE_PNG,
): ImageAttachment {
  return {
    id: "image-1",
    name: "pixel.png",
    source: "picker",
    media_type: "image/png",
    data_base64: bytes.toString("base64"),
    byte_length: bytes.length,
    content_sha256: createHash("sha256").update(bytes).digest("hex"),
    width: 1,
    height: 1,
    ...overrides,
  };
}

describe("canonical image attachment validation", () => {
  it("accepts the additive Autowork wire shape and returns exact verified bytes", () => {
    const wire = attachment();
    const parsed = parseClientMessage({
      type: "chat_agent",
      role: "analyst",
      message: "inspect this",
      image_attachments: [wire],
    });
    expect(parsed.type).toBe("chat_agent");
    const [validated] = validateImageAttachments([wire]);
    expect(Buffer.from(validated!.data)).toEqual(ONE_BY_ONE_PNG);
    expect(validated).toMatchObject({
      id: wire.id,
      mediaType: "image/png",
      byteLength: ONE_BY_ONE_PNG.length,
      width: 1,
      height: 1,
    });
  });

  it.each([
    ["byte length", { byte_length: ONE_BY_ONE_PNG.length + 1 }],
    ["hash", { content_sha256: "0".repeat(64) }],
    ["dimensions", { width: 2 }],
    ["RGBA budget", { width: 8192, height: 8192 }],
  ])("rejects an integrity mismatch in %s", (_label, overrides) => {
    expect(() => validateImageAttachments([attachment(overrides as Partial<ImageAttachment>)]))
      .toThrow(ImageAttachmentValidationError);
  });

  it("rejects malformed and non-canonical base64", () => {
    const wire = attachment({ data_base64: "ZE==", byte_length: 1 });
    expect(() => validateImageAttachments([wire])).toThrow(/canonical base64/);
    expect(() => validateImageAttachments([attachment({ data_base64: "@@@@" })]))
      .toThrow(/malformed base64/);
  });

  it("rejects duplicate IDs and duplicate exact content", () => {
    expect(() => validateImageAttachments([
      attachment(),
      attachment({ name: "other.png" }),
    ])).toThrow(/duplicates/);
    expect(() => validateImageAttachments([
      attachment(),
      attachment({ id: "image-2" }),
    ])).toThrow(/content_sha256 duplicates/);
  });

  it("enforces image count and aggregate encoded payload limits", () => {
    expect(() => validateImageAttachments(Array.from({ length: 5 }, (_, index) =>
      attachment({ id: `image-${index}` }, Buffer.concat([ONE_BY_ONE_PNG, Buffer.from([index])])))))
      .toThrow();

    const large = Buffer.alloc(MAX_IMAGE_SOURCE_BYTES);
    ONE_BY_ONE_PNG.copy(large);
    expect(() => validateImageAttachments(Array.from({ length: 3 }, (_, index) =>
      attachment({ id: `large-${index}` }, Buffer.from(large).fill(index, ONE_BY_ONE_PNG.length)))))
      .toThrow(/aggregate encoded limit/);
  });

  it("rejects before invoking the chat provider workflow", async () => {
    const chatAgent = vi.fn();
    await expect(executeChatAgentCommand({
      command: {
        type: "chat_agent",
        role: "analyst",
        message: "inspect",
        image_attachments: [attachment({ content_sha256: "0".repeat(64) })],
      },
      runManager: { chatAgent },
    })).rejects.toThrow(/content_sha256/);
    expect(chatAgent).not.toHaveBeenCalled();
  });
});

describe("provider image delivery", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends exact validated bytes in Anthropic image blocks", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      content: [{ type: "text", text: "done" }],
      model: "claude-sonnet-5",
      usage: { input_tokens: 1, output_tokens: 1 },
      stop_reason: "end_turn",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const provider = createAnthropicProvider({ apiKey: "test" });
    const [image] = validateImageAttachments([attachment()]);
    await provider.complete({ systemPrompt: "system", userPrompt: "operator text", imageAttachments: [image!] });
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1]!.body)) as Record<string, unknown>;
    const messages = body.messages as Array<{ content: Array<Record<string, unknown>> }>;
    expect(messages[0]!.content).toEqual([
      {
        type: "image",
        source: { type: "base64", media_type: "image/png", data: ONE_BY_ONE_PNG.toString("base64") },
      },
      { type: "text", text: "operator text" },
    ]);
  });

  it("sends exact bytes in OpenAI image parts and fails closed for unknown gateways", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      choices: [{ message: { content: "done" }, finish_reason: "stop" }],
      model: "gpt-5.6-terra",
      usage: { prompt_tokens: 1, completion_tokens: 1 },
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const [image] = validateImageAttachments([attachment()]);
    const provider = createOpenAICompatibleProvider({ apiKey: "test", model: "gpt-5.6-terra" });
    await provider.complete({ systemPrompt: "system", userPrompt: "operator text", imageAttachments: [image!] });
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1]!.body)) as Record<string, unknown>;
    const messages = body.messages as Array<{ role: string; content: unknown }>;
    expect(messages[1]!.content).toEqual([
      { type: "text", text: "operator text" },
      { type: "image_url", image_url: { url: `data:image/png;base64,${ONE_BY_ONE_PNG.toString("base64")}` } },
    ]);

    const unknownGateway = createOpenAICompatibleProvider({
      baseUrl: "https://gateway.example/v1",
      model: "gpt-5.6-terra",
    });
    await expect(unknownGateway.complete({
      systemPrompt: "system",
      userPrompt: "operator text",
      imageAttachments: [image!],
    })).rejects.toThrow(/does not support image attachments/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("allows an explicitly opted-in compatible gateway", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      choices: [{ message: { content: "done" }, finish_reason: "stop" }],
      model: "vision-model",
      usage: { prompt_tokens: 1, completion_tokens: 1 },
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const provider = createOpenAICompatibleProvider({
      baseUrl: "https://gateway.example/v1",
      model: "vision-model",
      imageSupport: true,
    });
    const [image] = validateImageAttachments([attachment()]);
    await expect(provider.complete({
      systemPrompt: "system",
      userPrompt: "operator text",
      imageAttachments: [image!],
    })).resolves.toMatchObject({ text: "done" });
  });

  it("advertises image capability only when dynamically supplied", () => {
    const incapable = buildHelloMessage({ runTranscript: true });
    const capable = buildHelloMessage({
      runTranscript: true,
      capabilities: [IMAGE_ATTACHMENTS_CAPABILITY],
    });
    expect(incapable.type).toBe("hello");
    expect(capable.type).toBe("hello");
    if (incapable.type !== "hello" || capable.type !== "hello") throw new Error("expected hello");
    expect(incapable.capabilities).not.toContain(IMAGE_ATTACHMENTS_CAPABILITY);
    expect(capable.capabilities).toContain(IMAGE_ATTACHMENTS_CAPABILITY);
  });
});
