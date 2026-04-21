/**
 * Fake-fetch test helper for instrumentClient tests.
 * Task 3.6 — lands with Task 3.7 tests.
 */

export function makeFakeFetch(
  responder: (url: string, init: RequestInit) => Response,
): typeof fetch {
  return (async (input, init) => {
    const url = typeof input === "string" ? input : (input as Request).url;
    return responder(url, (init ?? {}) as RequestInit);
  }) as typeof fetch;
}

export function cannedChatCompletion(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "chatcmpl-fake",
    object: "chat.completion",
    created: 1714000000,
    model: "gpt-4o",
    choices: [
      {
        index: 0,
        message: { role: "assistant", content: "hello world" },
        finish_reason: "stop",
      },
    ],
    usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    ...overrides,
  };
}

export function cannedChatCompletionWithToolCall(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "chatcmpl-fake-tool",
    object: "chat.completion",
    created: 1714000000,
    model: "gpt-4o",
    choices: [
      {
        index: 0,
        message: {
          role: "assistant",
          content: null,
          tool_calls: [
            {
              id: "call_abc123",
              type: "function",
              function: { name: "get_weather", arguments: '{"location":"New York"}' },
            },
          ],
        },
        finish_reason: "tool_calls",
      },
    ],
    usage: { prompt_tokens: 15, completion_tokens: 10, total_tokens: 25 },
    ...overrides,
  };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export function errorResponse(status: number, message: string): Response {
  return new Response(
    JSON.stringify({ error: { message, type: "api_error", code: null } }),
    { status, headers: { "content-type": "application/json" } },
  );
}

/** Build an SSE stream from an array of data objects */
export function sseStream(chunks: unknown[], includeUsage?: Record<string, unknown>): Response {
  const lines: string[] = [];
  for (const chunk of chunks) {
    lines.push(`data: ${JSON.stringify(chunk)}\n\n`);
  }
  if (includeUsage) {
    lines.push(`data: ${JSON.stringify({ ...chunks[chunks.length - 1], usage: includeUsage })}\n\n`);
  }
  lines.push("data: [DONE]\n\n");
  const body = lines.join("");
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}
