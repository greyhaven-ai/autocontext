/**
 * Fake-fetch helpers for Anthropic integration tests.
 * Constructs SSE streams and JSON responses in Anthropic's event format.
 */

export function makeFakeFetch(
  responder: (url: string, init: RequestInit) => Response,
): typeof fetch {
  return (async (input, init) => {
    const url = typeof input === "string" ? input : (input as Request).url;
    return responder(url, (init ?? {}) as RequestInit);
  }) as typeof fetch;
}

export function cannedMessagesResponse(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    id: "msg_fake",
    type: "message",
    role: "assistant",
    model: "claude-sonnet-4-5-20250514",
    content: [{ type: "text", text: "hello world" }],
    stop_reason: "end_turn",
    stop_sequence: null,
    usage: { input_tokens: 10, output_tokens: 5 },
    ...overrides,
  };
}

export function cannedMessagesResponseWithToolCall(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    id: "msg_fake_tool",
    type: "message",
    role: "assistant",
    model: "claude-sonnet-4-5-20250514",
    content: [
      { type: "text", text: "I'll use the tool." },
      {
        type: "tool_use",
        id: "toolu_01",
        name: "get_weather",
        input: { location: "London" },
      },
    ],
    stop_reason: "tool_use",
    stop_sequence: null,
    usage: { input_tokens: 15, output_tokens: 8 },
    ...overrides,
  };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export function errorResponse(
  status: number,
  message: string,
  errorType = "api_error",
): Response {
  return new Response(
    JSON.stringify({
      type: "error",
      error: { type: errorType, message },
    }),
    { status, headers: { "content-type": "application/json" } },
  );
}

export function anthropicSseStream(events: Record<string, unknown>[]): Response {
  const lines: string[] = [];
  for (const ev of events) {
    lines.push(`event: ${ev["type"] as string}\ndata: ${JSON.stringify(ev)}\n\n`);
  }
  return new Response(lines.join(""), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

export function cannedAnthropicSseResponse(
  opts: {
    textPieces?: string[];
    toolUse?: {
      id: string;
      name: string;
      inputJsonDeltaChunks: string[];
    };
    usage?: { input_tokens: number; output_tokens: number };
    stopReason?: string;
  } = {},
): Response {
  const pieces = opts.textPieces ?? ["hello", " world"];
  const events: Record<string, unknown>[] = [];

  events.push({
    type: "message_start",
    message: {
      id: "msg_fake",
      role: "assistant",
      content: [],
      usage: opts.usage ?? { input_tokens: 1, output_tokens: 0 },
    },
  });

  events.push({
    type: "content_block_start",
    index: 0,
    content_block: { type: "text", text: "" },
  });

  for (const p of pieces) {
    events.push({
      type: "content_block_delta",
      index: 0,
      delta: { type: "text_delta", text: p },
    });
  }

  events.push({ type: "content_block_stop", index: 0 });

  if (opts.toolUse) {
    const idx = 1;
    events.push({
      type: "content_block_start",
      index: idx,
      content_block: {
        type: "tool_use",
        id: opts.toolUse.id,
        name: opts.toolUse.name,
        input: {},
      },
    });
    for (const chunk of opts.toolUse.inputJsonDeltaChunks) {
      events.push({
        type: "content_block_delta",
        index: idx,
        delta: { type: "input_json_delta", partial_json: chunk },
      });
    }
    events.push({ type: "content_block_stop", index: idx });
  }

  events.push({
    type: "message_delta",
    delta: { stop_reason: opts.stopReason ?? "end_turn", stop_sequence: null },
    usage: { output_tokens: pieces.length },
  });

  events.push({ type: "message_stop" });

  return anthropicSseStream(events);
}
