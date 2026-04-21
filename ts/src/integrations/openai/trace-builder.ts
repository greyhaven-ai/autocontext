/**
 * Helpers for assembling ProductionTrace objects from OpenAI requests/responses.
 *
 * Uses buildTrace from autoctx/production-traces as the validation-and-shape
 * source of truth. Redaction of error messages happens here. Mirror of Python
 * ``_trace_builder.py``.
 */
import { buildTrace } from "../../production-traces/sdk/build-trace.js";
import type { ProductionTrace } from "../../production-traces/contract/types.js";

// Conservative secret-literal regex set. Matches the shapes the production-traces
// redaction scanner looks for. Kept narrow on purpose — this is best-effort
// last-line-of-defense, NOT the authoritative redactor.
const _SECRET_PATTERNS = [
  /sk-[A-Za-z0-9]{20,}/g,
  /AKIA[0-9A-Z]{16}/g,
  /xoxb-[A-Za-z0-9-]{10,}/g,
];

function _redact(msg: string): string {
  let result = msg;
  for (const pat of _SECRET_PATTERNS) {
    result = result.replace(pat, "<redacted>");
  }
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

export function normalizeMessages(
  messages: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  const ts = _nowIso();
  return messages.map((msg) => {
    if ("timestamp" in msg) return msg;
    return { ...msg, timestamp: ts };
  });
}

export function normalizeToolCalls(
  toolCalls: Array<Record<string, unknown>> | null | undefined,
): Array<{ toolName: string; args: Record<string, unknown> }> | null {
  if (!toolCalls || toolCalls.length === 0) return null;
  const result: Array<{ toolName: string; args: Record<string, unknown> }> = [];
  for (const tc of toolCalls) {
    if ("function" in tc) {
      const fn = tc["function"] as Record<string, unknown>;
      let args: Record<string, unknown>;
      try {
        const raw = fn["arguments"];
        args = typeof raw === "string" ? (JSON.parse(raw) as Record<string, unknown>) : {};
      } catch {
        args = { _raw: String(fn["arguments"] ?? "") };
      }
      result.push({ toolName: String(fn["name"] ?? ""), args });
    } else if ("toolName" in tc) {
      // Already in schema format
      result.push({
        toolName: String(tc["toolName"]),
        args: (tc["args"] as Record<string, unknown>) ?? {},
      });
    }
  }
  return result.length > 0 ? result : null;
}

export function buildRequestSnapshot(opts: {
  model: string;
  messages: Array<Record<string, unknown>>;
  extraKwargs: Record<string, unknown>;
}): RequestSnapshot {
  return { model: opts.model, messages: opts.messages, extra: opts.extraKwargs };
}

function _mapUsage(
  responseUsage: Record<string, unknown> | null | undefined,
): { tokensIn: number; tokensOut: number } {
  if (!responseUsage) return { tokensIn: 0, tokensOut: 0 };
  return {
    tokensIn: Number(
      responseUsage["prompt_tokens"] ?? responseUsage["input_tokens"] ?? 0,
    ),
    tokensOut: Number(
      responseUsage["completion_tokens"] ?? responseUsage["output_tokens"] ?? 0,
    ),
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

export function buildSuccessTrace(opts: {
  requestSnapshot: RequestSnapshot;
  responseUsage: Record<string, unknown> | null | undefined;
  responseToolCalls: Array<Record<string, unknown>> | null | undefined;
  identity: Record<string, string>;
  timing: { startedAt: string; endedAt: string; latencyMs: number; timeToFirstTokenMs?: number };
  env: { environmentTag: string; appId: string };
  sourceInfo: { emitter: string; sdk: { name: string; version: string } };
  traceId: string;
}): ProductionTrace {
  const toolCalls = normalizeToolCalls(opts.responseToolCalls);
  return buildTrace({
    provider: "openai",
    model: opts.requestSnapshot.model,
    messages: normalizeMessages(opts.requestSnapshot.messages) as unknown as Parameters<typeof buildTrace>[0]["messages"],
    timing: opts.timing,
    usage: _mapUsage(opts.responseUsage),
    env: opts.env as Parameters<typeof buildTrace>[0]["env"],
    source: opts.sourceInfo,
    toolCalls: (toolCalls ?? []) as Parameters<typeof buildTrace>[0]["toolCalls"],
    session: _identityToSession(opts.identity) as Parameters<typeof buildTrace>[0]["session"],
    outcome: { label: "success" },
    traceId: opts.traceId,
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
    provider: "openai",
    model: opts.requestSnapshot.model,
    messages: normalizeMessages(opts.requestSnapshot.messages) as unknown as Parameters<typeof buildTrace>[0]["messages"],
    timing: opts.timing,
    usage: { tokensIn: 0, tokensOut: 0 },
    env: opts.env as Parameters<typeof buildTrace>[0]["env"],
    source: opts.sourceInfo,
    session: _identityToSession(opts.identity) as Parameters<typeof buildTrace>[0]["session"],
    outcome: { label: "failure", error: errorObj as { type: string; message: string; stack?: string } },
    traceId: opts.traceId,
  });
}

export function finalizeStreamingTrace(opts: {
  requestSnapshot: RequestSnapshot;
  identity: Record<string, string>;
  timing: { startedAt: string; endedAt: string; latencyMs: number };
  env: { environmentTag: string; appId: string };
  sourceInfo: { emitter: string; sdk: { name: string; version: string } };
  traceId: string;
  accumulatedUsage: Record<string, unknown> | null | undefined;
  accumulatedToolCalls: Array<Record<string, unknown>> | null | undefined;
  outcome: Record<string, unknown>;
}): ProductionTrace {
  const toolCalls = normalizeToolCalls(opts.accumulatedToolCalls);
  return buildTrace({
    provider: "openai",
    model: opts.requestSnapshot.model,
    messages: normalizeMessages(opts.requestSnapshot.messages) as unknown as Parameters<typeof buildTrace>[0]["messages"],
    timing: opts.timing,
    usage: _mapUsage(opts.accumulatedUsage),
    env: opts.env as Parameters<typeof buildTrace>[0]["env"],
    source: opts.sourceInfo,
    toolCalls: (toolCalls ?? []) as Parameters<typeof buildTrace>[0]["toolCalls"],
    session: _identityToSession(opts.identity) as Parameters<typeof buildTrace>[0]["session"],
    outcome: opts.outcome as Parameters<typeof buildTrace>[0]["outcome"],
    traceId: opts.traceId,
  });
}
