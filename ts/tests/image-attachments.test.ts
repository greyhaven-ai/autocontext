import { createHash } from "node:crypto";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  IMAGE_ATTACHMENTS_CAPABILITY,
  parseClientMessage,
} from "../src/server/protocol.js";
import { executeChatAgentCommand } from "../src/server/chat-agent-command-workflow.js";
import { executeInteractiveControlCommand } from "../src/server/interactive-control-command-workflow.js";
import { buildHelloMessage } from "../src/server/websocket-session-bootstrap.js";
import {
  createAnthropicProvider,
  createOpenAICompatibleProvider,
} from "../src/providers/provider-factory.js";
import {
  ImageAttachmentValidationError,
  MAX_IMAGE_SOURCE_BYTES,
  validateImageAttachments,
  validateImageAttachmentsForInference,
  type ImageAttachment,
} from "../src/types/image-attachments.js";

const ONE_BY_ONE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);
const ONE_BY_ONE_GIF = Buffer.from(
  "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAkwBADs=",
  "base64",
);
const TWO_BY_TWO_JPEG = Buffer.from(
  "/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYyLjExLjEwMAD/2wBDAAgEBAQEBAUFBQUFBQYGBgYGBgYGBgYGBgYHBwcICAgHBwcGBgcHCAgICAkJCQgICAgJCQoKCgwMCwsODg4RERT/xABLAAEBAAAAAAAAAAAAAAAAAAAACAEBAAAAAAAAAAAAAAAAAAAAABABAAAAAAAAAAAAAAAAAAAAABEBAAAAAAAAAAAAAAAAAAAAAP/AABEIAAIAAgMBIgACEQADEQD/2gAMAwEAAhEDEQA/AJ/AB//Z",
  "base64",
);
const TWO_BY_TWO_WEBP = Buffer.from(
  "UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoCAAIAAgA0JaQAA3AA/vv9UAA=",
  "base64",
);

function pngCrc32(bytes: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ ((crc & 1) === 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(typeName: string, payload: Buffer): Buffer {
  const type = Buffer.from(typeName, "ascii");
  const chunk = Buffer.alloc(12 + payload.length);
  chunk.writeUInt32BE(payload.length, 0);
  type.copy(chunk, 4);
  payload.copy(chunk, 8);
  chunk.writeUInt32BE(pngCrc32(Buffer.concat([type, payload])), 8 + payload.length);
  return chunk;
}

function paddedPng(seed: number): Buffer {
  const payload = Buffer.alloc(MAX_IMAGE_SOURCE_BYTES - ONE_BY_ONE_PNG.length - 12);
  payload[0] = 0x78;
  payload[1] = 0;
  payload[2] = seed;
  return Buffer.concat([
    ONE_BY_ONE_PNG.subarray(0, ONE_BY_ONE_PNG.length - 12),
    pngChunk("tEXt", payload),
    ONE_BY_ONE_PNG.subarray(ONE_BY_ONE_PNG.length - 12),
  ]);
}

function attachment(
  overrides: Partial<ImageAttachment> = {},
  bytes: Buffer<ArrayBufferLike> = ONE_BY_ONE_PNG,
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

  it("rejects header-only, truncated, corrupt, and animated image containers", () => {
    const corruptPng = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZsOkAAAAASUVORK5CYII=",
      "base64",
    );
    const truncatedGif = ONE_BY_ONE_GIF.subarray(0, 10);
    const jpegWithoutScan = Buffer.from(
      "ffd8ffc0000b080001000101011100ffd9",
      "hex",
    );
    const jpegWithoutEntropyData = Buffer.from(
      "ffd8ffc0000b080001000101011100ffda0008010100003f00ffd9",
      "hex",
    );
    const webpWithoutFrame = Buffer.from(
      "524946461600000057454250565038580a00000000000000000000",
      "hex",
    );
    const headerOnlyLosslessWebp = Buffer.from(
      "5249464612000000574542505650384c050000002f0000000000",
      "hex",
    );
    const apngControl = Buffer.alloc(8);
    apngControl.writeUInt32BE(1, 0);
    const animatedPng = Buffer.concat([
      ONE_BY_ONE_PNG.subarray(0, 33),
      pngChunk("acTL", apngControl),
      ONE_BY_ONE_PNG.subarray(33),
    ]);
    const animatedWebp = Buffer.from(
      "524946461600000057454250565038580a00000002000000000000000000",
      "hex",
    );
    const gifImageBlock = ONE_BY_ONE_GIF.subarray(19, ONE_BY_ONE_GIF.length - 1);
    const animatedGif = Buffer.concat([
      ONE_BY_ONE_GIF.subarray(0, ONE_BY_ONE_GIF.length - 1),
      gifImageBlock,
      Buffer.from([0x3b]),
    ]);

    for (const [mediaType, name, bytes] of [
      ["image/png", "corrupt.png", corruptPng],
      ["image/gif", "truncated.gif", truncatedGif],
      ["image/jpeg", "header-only.jpg", jpegWithoutScan],
      ["image/jpeg", "empty-scan.jpg", jpegWithoutEntropyData],
      ["image/webp", "header-only.webp", webpWithoutFrame],
      ["image/webp", "header-only-lossless.webp", headerOnlyLosslessWebp],
      ["image/png", "animated.png", animatedPng],
      ["image/gif", "animated.gif", animatedGif],
      ["image/webp", "animated.webp", animatedWebp],
    ] as const) {
      expect(() => validateImageAttachments([
        attachment({ media_type: mediaType, name }, bytes),
      ])).toThrow(/not a valid/);
    }
  });

  it("accepts a structurally complete single-frame GIF", () => {
    const [validated] = validateImageAttachments([
      attachment({ media_type: "image/gif", name: "pixel.gif" }, ONE_BY_ONE_GIF),
    ]);
    expect(validated).toMatchObject({ mediaType: "image/gif", width: 1, height: 1 });
  });

  it("accepts PNG filter-row overhead at the exact decoded RGBA boundary", async () => {
    const { default: sharp } = await import("sharp");
    const width = 4_096;
    const height = 4_096;
    const bytes = await sharp({
      create: {
        width,
        height,
        channels: 4,
        background: { r: 0, g: 0, b: 0, alpha: 0 },
      },
    }).png({ compressionLevel: 9 }).toBuffer();
    const wire = attachment({ width, height }, bytes);

    expect(bytes.length).toBeLessThan(MAX_IMAGE_SOURCE_BYTES);
    expect(() => validateImageAttachments([wire])).not.toThrow();
  }, 15_000);

  it.each([
    ["image/jpeg", "pixel.jpg", TWO_BY_TWO_JPEG],
    ["image/webp", "pixel.webp", TWO_BY_TWO_WEBP],
  ] as const)("accepts a structurally complete %s container", (mediaType, name, bytes) => {
    const [validated] = validateImageAttachments([
      attachment({ media_type: mediaType, name, width: 2, height: 2 }, bytes),
    ]);
    expect(validated).toMatchObject({ mediaType, width: 2, height: 2 });
  });

  it.each([
    ["image/png", "pixel.png", ONE_BY_ONE_PNG, 1, 1],
    ["image/gif", "pixel.gif", ONE_BY_ONE_GIF, 1, 1],
    ["image/jpeg", "pixel.jpg", TWO_BY_TWO_JPEG, 2, 2],
    ["image/webp", "pixel.webp", TWO_BY_TWO_WEBP, 2, 2],
  ] as const)("fully decodes a valid %s before inference", async (
    mediaType,
    name,
    bytes,
    width,
    height,
  ) => {
    await expect(validateImageAttachmentsForInference([
      attachment({ media_type: mediaType, name, width, height }, bytes),
    ])).resolves.toHaveLength(1);
  });

  it.each([
    [
      "image/jpeg",
      "corrupt-entropy.jpg",
      Buffer.from("ffd8ffc0000b080001000101011100ffda0008010100003f0000ffd9", "hex"),
    ],
    [
      "image/webp",
      "corrupt-partition.webp",
      Buffer.from("524946461800000057454250565038200c0000002000009d012a010001000000", "hex"),
    ],
  ] as const)("rejects malformed %s entropy during full decode", async (
    mediaType,
    name,
    bytes,
  ) => {
    const wire = attachment({ media_type: mediaType, name }, bytes);
    expect(() => validateImageAttachments([wire])).not.toThrow();
    await expect(validateImageAttachmentsForInference([wire])).rejects.toThrow(/fully decoded/);
  });

  it("fully decodes before either image-bearing command reaches RunManager", async () => {
    const corruptJpeg = Buffer.from(
      "ffd8ffc0000b080001000101011100ffda0008010100003f0000ffd9",
      "hex",
    );
    const wire = attachment({ media_type: "image/jpeg", name: "corrupt.jpg" }, corruptJpeg);
    const chatAgent = vi.fn();
    await expect(executeChatAgentCommand({
      command: {
        type: "chat_agent",
        role: "analyst",
        message: "inspect",
        image_attachments: [wire],
      },
      runManager: { getState: () => ({ runId: null }), chatAgent },
    })).rejects.toThrow(/fully decoded/);
    expect(chatAgent).not.toHaveBeenCalled();

    const injectHint = vi.fn();
    await expect(executeInteractiveControlCommand({
      command: {
        type: "inject_hint",
        text: "inspect",
        image_attachments: [wire],
      },
      runManager: {
        pause: vi.fn(),
        resume: vi.fn(),
        injectHint,
        overrideGate: vi.fn(),
        startRun: vi.fn(),
        getEnvironmentInfo: vi.fn(),
      },
    })).rejects.toThrow(/fully decoded/);
    expect(injectHint).not.toHaveBeenCalled();
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

    expect(() => validateImageAttachments(Array.from({ length: 3 }, (_, index) =>
      attachment({ id: `large-${index}` }, paddedPng(index)))))
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
      runManager: { getState: () => ({ runId: null }), chatAgent },
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

  it.each([
    ["gpt-4-turbo", true],
    ["gpt-4-turbo-2024-04-09", true],
    ["gpt-4-vision-preview", true],
    ["gpt-4o-mini", true],
    ["openai/gpt-4.1-nano", true],
    ["gpt-5.6-terra", true],
    ["o1", true],
    ["o1-pro", true],
    ["o3", true],
    ["o4-mini", true],
    ["o1-mini", false],
    ["o1-mini-2024-09-12", false],
    ["o1-preview", false],
    ["o3-mini", false],
    ["o3-mini-2025-01-31", false],
    ["gpt-4-turbo-preview", false],
    ["gpt-4", false],
    ["unknown-vision-model", false],
  ])("reports official OpenAI image support for %s", (model, expected) => {
    const provider = createOpenAICompatibleProvider({ apiKey: "test", model });
    expect(provider.supportsImageAttachments?.(model)).toBe(expected);
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
