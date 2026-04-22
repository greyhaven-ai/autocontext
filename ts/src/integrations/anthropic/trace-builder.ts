/**
 * Helpers for assembling ProductionTrace objects from Anthropic requests/responses.
 *
 * Handles cache-aware usage accounting, content-block flattening, and
 * stop-reason metadata. Mirror of Python _trace_builder.py for Anthropic.
 */
import { buildTrace } from "../../production-traces/sdk/build-trace.js";
import type { ProductionTrace } from "../../production-traces/contract/types.js";
import { flattenContent, extractToolUses, type ContentBlock } from "./content.js";

// Conservative secret-literal regex set, plus Anthropic key shapes.
const _SECRET_PATTERNS = [
  /sk-[A-Za-z0-9]{20,}/g,
  /sk-ant-[A-Za-z0-9_-]{40,}/g,
  /AKIA[0-9A-Z]{16}/g,
  /xoxb-[A-Za-z0-9-]{10,}/g,
];

function _redact(msg: string): string {
  let result = msg;
  for (const pat of _SECRET_PATTERNS) result = result.replace(pat, "<redacted>");
  return result;
}

function _nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

export type RequestSnapshot = {
  model: string;
  messages: Array<Record<string, unknown>>;
  extra: Record<string, unknown>;
};

export function buildRequestSnapshot(opts: {
  model: string;
  messages: Array<Record<string, unknown>>;
  extraKwargs: Record<string, unknown>;
}): RequestSnapshot {
  return { model: opts.model, messages: opts.messages, extra: opts.extraKwargs };
}

function _mapUsage(responseUsage: Record<string, unknown> | null | undefined): {
  tokensIn: number;
  tokensOut: number;
  providerUsage: Record<string, number>;
} {
  if (!responseUsage) {
    return {
      tokensIn: 0,
      tokensOut: 0,
      providerUsage: {
        inputTokens: 0,
        cacheCreationInputTokens: 0,
        cacheReadInputTokens: 0,
        outputTokens: 0,
      },
    };
  }
  const inputTokens = Number(responseUsage["input_tokens"] ?? 0);
  const cacheCreate = Number(responseUsage["cache_creation_input_tokens"] ?? 0);
  const cacheRead = Number(responseUsage["cache_read_input_tokens"] ?? 0);
  const outputTokens = Number(responseUsage["output_tokens"] ?? 0);
  return {
    tokensIn: inputTokens + cacheCreate + cacheRead,
    tokensOut: outputTokens,
    providerUsage: {
      inputTokens,
      cacheCreationInputTokens: cacheCreate,
      cacheReadInputTokens: cacheRead,
      outputTokens,
    },
  };
}

function _identityToSession(
  identity: Record<string, string>,
): Record<string, string> | undefined {
  const out: Record<string, string> = {};
  if (identity["user_id_hash"]) out["userIdHash"] = identity["user_id_hash"];
  if (identity["session_id_hash"]) out["sessionIdHash"] = identity["session_id_hash"];
  return Object.keys(out).length > 0 ? out : undefined;
}

function _normalizeRequestMessages(
  messages: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  const ts = _nowIso();
  return messages.map((msg) => ("timestamp" in msg ? msg : { ...msg, timestamp: ts }));
}

export function buildSuccessTrace(opts: {
  requestSnapshot: RequestSnapshot;
  responseContent: ContentBlock[] | string;
  responseUsage: Record<string, unknown> | null | undefined;
  responseStopReason: string | null | undefined;
  identity: Record<string, string>;
  timing: { startedAt: string; endedAt: string; latencyMs: number };
  env: { environmentTag: string; appId: string };
  sourceInfo: { emitter: string; sdk: { name: string; version: string } };
  traceId: string;
}): ProductionTrace {
  const ts = _nowIso();
  const normalizedMessages = _normalizeRequestMessages(opts.requestSnapshot.messages);
  normalizedMessages.push({
    role: "assistant",
    content: flattenContent(opts.responseContent as ContentBlock[]),
    timestamp: ts,
  });
  const toolCalls = extractToolUses(opts.responseContent as ContentBlock[]);
  const usage = _mapUsage(opts.responseUsage);
  const metadata = opts.responseStopReason
    ? { anthropicStopReason: opts.responseStopReason }
    : undefined;
  return buildTrace({
    provider: "anthropic",
    model: opts.requestSnapshot.model,
    messages: normalizedMessages as unknown as Parameters<typeof buildTrace>[0]["messages"],
    timing: opts.timing,
    usage: { tokensIn: usage.tokensIn, tokensOut: usage.tokensOut },
    env: opts.env as Parameters<typeof buildTrace>[0]["env"],
    source: opts.sourceInfo,
    toolCalls: (toolCalls ?? []) as Parameters<typeof buildTrace>[0]["toolCalls"],
    session: _identityToSession(opts.identity) as Parameters<typeof buildTrace>[0]["session"],
    outcome: { label: "success" },
    traceId: opts.traceId,
    ...(metadata ? { metadata } : {}),
  });
}

export function buildFailureTrace(opts: {
  requestSnapshot: RequestSnapshot;
  identity: Record<string, string>;
  timing: { startedAt: string; endedAt: string; latencyMs: number };
  env: { environmentTag: string; appId: string };
  sourceInfo: { emitter: string; sdk: { name: string; version: string } };
  traceId: string;
  reasonKey: string;
  errorMessage: string;
  stack: string | null;
}): ProductionTrace {
  const errorObj: Record<string, unknown> = {
    type: opts.reasonKey,
    message: _redact(opts.errorMessage),
  };
  if (opts.stack !== null) errorObj["stack"] = opts.stack;
  return buildTrace({
    provider: "anthropic",
    model: opts.requestSnapshot.model,
    messages: _normalizeRequestMessages(
      opts.requestSnapshot.messages,
    ) as unknown as Parameters<typeof buildTrace>[0]["messages"],
    timing: opts.timing,
    usage: { tokensIn: 0, tokensOut: 0 },
    env: opts.env as Parameters<typeof buildTrace>[0]["env"],
    source: opts.sourceInfo,
    session: _identityToSession(opts.identity) as Parameters<typeof buildTrace>[0]["session"],
    outcome: {
      label: "failure",
      error: errorObj as { type: string; message: string; stack?: string },
    },
    traceId: opts.traceId,
  });
}

export type AccumulatedBlock = {
  type: string;
  buffer: string;
  id?: string;
  name?: string;
  finalizedInput?: Record<string, unknown>;
};

export function finalizeStreamingTrace(opts: {
  requestSnapshot: RequestSnapshot;
  identity: Record<string, string>;
  timing: { startedAt: string; endedAt: string; latencyMs: number };
  env: { environmentTag: string; appId: string };
  sourceInfo: { emitter: string; sdk: { name: string; version: string } };
  traceId: string;
  accumulatedContentBlocks: Map<number, AccumulatedBlock>;
  accumulatedUsage: Record<string, unknown> | null | undefined;
  accumulatedStopReason: string | null | undefined;
  outcome: Record<string, unknown>;
}): ProductionTrace {
  const ts = _nowIso();
  // Reassemble content blocks in index order
  const indices = [...opts.accumulatedContentBlocks.keys()].sort((a, b) => a - b);
  const linearBlocks: ContentBlock[] = [];
  for (const idx of indices) {
    const block = opts.accumulatedContentBlocks.get(idx)!;
    if (block.type === "text") {
      linearBlocks.push({ type: "text", text: block.buffer });
    } else if (block.type === "tool_use") {
      linearBlocks.push({
        type: "tool_use",
        id: block.id ?? "",
        name: block.name ?? "",
        input: block.finalizedInput ?? {},
      });
    }
  }
  const normalizedMessages = _normalizeRequestMessages(opts.requestSnapshot.messages);
  normalizedMessages.push({
    role: "assistant",
    content: flattenContent(linearBlocks),
    timestamp: ts,
  });
  const toolCalls = extractToolUses(linearBlocks);
  const usage = _mapUsage(opts.accumulatedUsage);
  const metadata = opts.accumulatedStopReason
    ? { anthropicStopReason: opts.accumulatedStopReason }
    : undefined;
  return buildTrace({
    provider: "anthropic",
    model: opts.requestSnapshot.model,
    messages: normalizedMessages as unknown as Parameters<typeof buildTrace>[0]["messages"],
    timing: opts.timing,
    usage: { tokensIn: usage.tokensIn, tokensOut: usage.tokensOut },
    env: opts.env as Parameters<typeof buildTrace>[0]["env"],
    source: opts.sourceInfo,
    toolCalls: (toolCalls ?? []) as Parameters<typeof buildTrace>[0]["toolCalls"],
    session: _identityToSession(opts.identity) as Parameters<typeof buildTrace>[0]["session"],
    outcome: opts.outcome as Parameters<typeof buildTrace>[0]["outcome"],
    traceId: opts.traceId,
    ...(metadata ? { metadata } : {}),
  });
}
