/**
 * AC-682 (slice 1): bidirectional bridge between `PublicTrace` and a
 * minimal validated subset of OpenTelemetry JSON `ResourceSpans`.
 *
 * Scope: TypeScript only; the bridge is optional and does not replace
 * AutoContext's native trace schema. Python parity, OTLP protobuf wire
 * format, and the ProductionTrace bridge are out of scope.
 *
 * See `docs/opentelemetry-bridge.md` for the canonical attribute
 * vocabulary and the known-gap list (fields that don't round-trip
 * cleanly).
 *
 * OTel ID format (review feedback PR #959):
 *
 * - OTel span-context requires 32-hex-char traceIds and 16-hex-char
 *   spanIds. The bridge derives valid hex IDs deterministically by
 *   hashing the source PublicTrace identifiers so a round-trip emits
 *   identical IDs. The original `PublicTrace.traceId` is preserved as
 *   the `ai.trace.id` attribute on every span and used by the reverse
 *   path; the OTel-format traceId is opaque to PublicTrace consumers.
 */

import { createHash } from "node:crypto";

import { z } from "zod";

import {
  PublicTraceSchema,
  type PublicTrace,
  type ToolCall,
  type TraceMessage,
} from "./public-schema-contracts.js";

// ----------------------------------------------------------------------------
// OTel JSON shape (a small, validation-friendly subset)
// ----------------------------------------------------------------------------

const OtelAttributeValueSchema = z.union([z.string(), z.number(), z.boolean()]);

const OtelAttributesSchema = z.record(OtelAttributeValueSchema);

const OtelSpanStatusSchema = z.object({
  code: z.enum(["OK", "ERROR", "UNSET"]),
  message: z.string().optional(),
});

export const OtelSpanSchema = z.object({
  traceId: z.string().regex(/^[0-9a-f]{32}$/, "traceId must be 32 hex chars"),
  spanId: z.string().regex(/^[0-9a-f]{16}$/, "spanId must be 16 hex chars"),
  parentSpanId: z
    .string()
    .regex(/^[0-9a-f]{16}$/, "parentSpanId must be 16 hex chars")
    .optional(),
  name: z.string().min(1),
  kind: z.enum(["internal", "client", "server", "producer", "consumer"]).optional(),
  startTimeUnixNano: z.string().min(1),
  endTimeUnixNano: z.string().min(1).optional(),
  attributes: OtelAttributesSchema,
  status: OtelSpanStatusSchema.optional(),
});

export const OtelScopeSpansSchema = z.object({
  scope: z.object({ name: z.string(), version: z.string().optional() }),
  spans: z.array(OtelSpanSchema),
});

export const OtelResourceSpansSchema = z.object({
  resource: z.object({ attributes: OtelAttributesSchema }),
  scopeSpans: z.array(OtelScopeSpansSchema),
});

export type OtelAttributes = z.infer<typeof OtelAttributesSchema>;
export type OtelSpan = z.infer<typeof OtelSpanSchema>;
export type OtelScopeSpans = z.infer<typeof OtelScopeSpansSchema>;
export type OtelResourceSpans = z.infer<typeof OtelResourceSpansSchema>;

const SCOPE_NAME = "autocontext.public-trace";
const SCOPE_VERSION = "1.0.0";
const SPAN_NAME_MESSAGE_PREFIX = "message:";
const SPAN_NAME_TOOL_PREFIX = "tool:";

// Span / trace IDs are SHA-256-derived hex strings of the right widths so
// downstream OTel stores accept them. The PublicTrace's own traceId is
// preserved as the `ai.trace.id` attribute and is the source of truth on
// the reverse path; the OTel hex IDs are opaque correlation handles.
const TRACE_ID_HEX_LEN = 32;
const SPAN_ID_HEX_LEN = 16;

function sha256Hex(input: string): string {
  return createHash("sha256").update(input).digest("hex");
}

function deriveTraceIdHex(traceId: string): string {
  return sha256Hex(`trace|${traceId}`).slice(0, TRACE_ID_HEX_LEN);
}

function deriveSpanIdHex(traceId: string, slot: string): string {
  return sha256Hex(`span|${traceId}|${slot}`).slice(0, SPAN_ID_HEX_LEN);
}

// ----------------------------------------------------------------------------
// Forward: PublicTrace -> OtelResourceSpans
// ----------------------------------------------------------------------------

function isoToUnixNano(iso: string): string {
  // OTel proto uses uint64 nanoseconds-since-epoch; we serialize as a string
  // because JSON numbers can't hold the full uint64 range.
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "0";
  return `${BigInt(ms) * 1_000_000n}`;
}

function attributesForMessage(message: TraceMessage, index: number): OtelAttributes {
  const attrs: OtelAttributes = {
    "ai.role": message.role,
    "ai.content": message.content,
    "ai.message.index": index,
    "ai.message.timestamp": message.timestamp,
  };
  if (message.metadata !== undefined) {
    attrs["ai.message.metadata.json"] = JSON.stringify(message.metadata);
  }
  return attrs;
}

function attributesForToolCall(call: ToolCall, index: number): OtelAttributes {
  // `tool.index` is mandatory on emission so reverse import can reconstruct
  // tool-call order even when the OTel store reorders sibling spans.
  const attrs: OtelAttributes = {
    "tool.name": call.toolName,
    "tool.index": index,
    "tool.args.json": JSON.stringify(call.args ?? {}),
  };
  if (typeof call.durationMs === "number") {
    attrs["tool.duration_ms"] = call.durationMs;
  }
  if (typeof call.error === "string" && call.error.length > 0) {
    attrs["tool.error"] = call.error;
  }
  if (call.result !== undefined) {
    // Tool result payloads are unbounded; we serialize as JSON but flag this
    // as lossy in docs/opentelemetry-bridge.md.
    attrs["tool.result.json"] = JSON.stringify(call.result);
  }
  return attrs;
}

function rootSpanAttributes(trace: PublicTrace): OtelAttributes {
  const attrs: OtelAttributes = {
    "ai.trace.id": trace.traceId,
    "ai.trace.collectedAt": trace.collectedAt,
    "ai.trace.schemaVersion": trace.schemaVersion,
  };
  if (trace.sessionId !== undefined) {
    attrs["ai.session.id"] = trace.sessionId;
  }
  if (trace.outcome !== undefined) {
    attrs["ai.outcome.score"] = trace.outcome.score;
    attrs["ai.outcome.reasoning"] = trace.outcome.reasoning;
    for (const [name, value] of Object.entries(trace.outcome.dimensions ?? {})) {
      attrs[`ai.outcome.dimensions.${name}`] = value;
    }
  }
  if (trace.fileReferences !== undefined) {
    // Lossy: file references are a JSON blob inside a single attribute.
    attrs["ai.file_references.json"] = JSON.stringify(trace.fileReferences);
  }
  if (trace.redactions !== undefined) {
    attrs["ai.redactions.json"] = JSON.stringify(trace.redactions);
  }
  if (trace.metadata !== undefined) {
    attrs["ai.metadata.json"] = JSON.stringify(trace.metadata);
  }
  return attrs;
}

export function publicTraceToOtelResourceSpans(trace: PublicTrace): OtelResourceSpans {
  const traceIdHex = deriveTraceIdHex(trace.traceId);
  const rootSpanIdHex = deriveSpanIdHex(trace.traceId, "root");
  const spans: OtelSpan[] = [];
  const rootStart = isoToUnixNano(trace.collectedAt);

  spans.push({
    traceId: traceIdHex,
    spanId: rootSpanIdHex,
    name: `autocontext.run:${trace.traceId}`,
    kind: "internal",
    startTimeUnixNano: rootStart,
    attributes: rootSpanAttributes(trace),
  });

  trace.messages.forEach((message, index) => {
    const messageSpanIdHex = deriveSpanIdHex(trace.traceId, `msg-${index}`);
    spans.push({
      traceId: traceIdHex,
      spanId: messageSpanIdHex,
      parentSpanId: rootSpanIdHex,
      name: `${SPAN_NAME_MESSAGE_PREFIX}${message.role}`,
      kind: "internal",
      startTimeUnixNano: isoToUnixNano(message.timestamp),
      attributes: attributesForMessage(message, index),
    });

    (message.toolCalls ?? []).forEach((call, callIndex) => {
      const toolSpanIdHex = deriveSpanIdHex(trace.traceId, `tool-${index}-${callIndex}`);
      const span: OtelSpan = {
        traceId: traceIdHex,
        spanId: toolSpanIdHex,
        parentSpanId: messageSpanIdHex,
        name: `${SPAN_NAME_TOOL_PREFIX}${call.toolName}`,
        kind: "client",
        startTimeUnixNano: isoToUnixNano(message.timestamp),
        attributes: attributesForToolCall(call, callIndex),
      };
      if (typeof call.error === "string" && call.error.length > 0) {
        span.status = { code: "ERROR", message: call.error };
      }
      spans.push(span);
    });
  });

  return {
    resource: {
      attributes: {
        "service.name": trace.sourceHarness,
      },
    },
    scopeSpans: [
      {
        scope: { name: SCOPE_NAME, version: SCOPE_VERSION },
        spans,
      },
    ],
  };
}

// ----------------------------------------------------------------------------
// Reverse: OtelResourceSpans (or arbitrary `unknown` JSON) -> PublicTrace
// ----------------------------------------------------------------------------

export interface OtelToPublicTraceOk {
  readonly trace: PublicTrace;
}

export interface OtelToPublicTraceErr {
  readonly error: string;
}

export type OtelToPublicTraceResult = OtelToPublicTraceOk | OtelToPublicTraceErr;

function flatSpans(input: OtelResourceSpans): OtelSpan[] {
  return input.scopeSpans.flatMap((scope) => scope.spans);
}

function parseJsonAttr(attrs: OtelAttributes, key: string): unknown | undefined {
  const raw = attrs[key];
  if (typeof raw !== "string") return undefined;
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

function readOutcomeFromRoot(attrs: OtelAttributes): PublicTrace["outcome"] | undefined {
  const score = attrs["ai.outcome.score"];
  const reasoning = attrs["ai.outcome.reasoning"];
  if (typeof score !== "number" || typeof reasoning !== "string") {
    return undefined;
  }
  const dimensions: Record<string, number> = {};
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("ai.outcome.dimensions.") && typeof value === "number") {
      dimensions[key.slice("ai.outcome.dimensions.".length)] = value;
    }
  }
  return { score, reasoning, dimensions };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

type BuildMessageResult =
  | { kind: "ok"; message: Record<string, unknown> }
  | { kind: "err"; error: string };

function buildMessage(messageSpan: OtelSpan, toolSpans: readonly OtelSpan[]): BuildMessageResult {
  const role = messageSpan.attributes["ai.role"];
  const content = messageSpan.attributes["ai.content"];
  const timestamp = messageSpan.attributes["ai.message.timestamp"];
  if (typeof role !== "string" || typeof content !== "string" || typeof timestamp !== "string") {
    return {
      kind: "err",
      error: `message span ${messageSpan.spanId} missing ai.role/ai.content/ai.message.timestamp`,
    };
  }
  if (!["user", "assistant", "system", "tool"].includes(role)) {
    return { kind: "err", error: `message span ${messageSpan.spanId} has unknown role ${role}` };
  }
  const message: Record<string, unknown> = { role, content, timestamp };
  const metadata = parseJsonAttr(messageSpan.attributes, "ai.message.metadata.json");
  if (isRecord(metadata)) {
    message.metadata = metadata;
  }
  // Sort tool spans by their authoring index, not the order the OTel store
  // happened to deliver them. Falls back to position when `tool.index` is
  // missing (e.g., spans from a non-AutoContext producer).
  const orderedTools = [...toolSpans].sort((a, b) => {
    const ai = typeof a.attributes["tool.index"] === "number" ? a.attributes["tool.index"] : 0;
    const bi = typeof b.attributes["tool.index"] === "number" ? b.attributes["tool.index"] : 0;
    return ai - bi;
  });
  const calls: Record<string, unknown>[] = [];
  for (const toolSpan of orderedTools) {
    const toolName = toolSpan.attributes["tool.name"];
    if (typeof toolName !== "string") continue;
    const argsRaw = parseJsonAttr(toolSpan.attributes, "tool.args.json");
    const args = isRecord(argsRaw) ? argsRaw : {};
    const call: Record<string, unknown> = { toolName, args };
    const duration = toolSpan.attributes["tool.duration_ms"];
    if (typeof duration === "number") call.durationMs = duration;
    const error = toolSpan.attributes["tool.error"];
    if (typeof error === "string") call.error = error;
    const result = parseJsonAttr(toolSpan.attributes, "tool.result.json");
    if (result !== undefined) call.result = result;
    calls.push(call);
  }
  if (calls.length > 0) {
    message.toolCalls = calls;
  }
  return { kind: "ok", message };
}

export function otelResourceSpansToPublicTrace(input: unknown): OtelToPublicTraceResult {
  // Parse at the boundary so malformed external JSON cannot throw inside
  // the bridge. The reverse path then operates on the typed schema-parsed
  // value rather than dereferencing arbitrary input.
  const parsed = OtelResourceSpansSchema.safeParse(input);
  if (!parsed.success) {
    const issues = parsed.error.issues
      .map((i) => `${i.path.join(".") || "<root>"}: ${i.message}`)
      .join("; ");
    return { error: `OTel input failed OtelResourceSpansSchema validation: ${issues}` };
  }
  const valid = parsed.data;

  const sourceHarness = valid.resource.attributes["service.name"];
  if (typeof sourceHarness !== "string" || sourceHarness.length === 0) {
    return { error: "OTel ResourceSpans is missing resource.attributes['service.name']" };
  }
  const spans = flatSpans(valid);
  const root = spans.find((s) => s.parentSpanId === undefined);
  if (root === undefined) {
    return { error: "OTel ResourceSpans has no root span (every span has parentSpanId set)" };
  }

  // The PublicTrace's own traceId is preserved as ai.trace.id; the OTel
  // hex traceId on each span is just a correlation handle.
  const originalTraceId = root.attributes["ai.trace.id"];
  if (typeof originalTraceId !== "string" || originalTraceId.length === 0) {
    return { error: "OTel root span is missing ai.trace.id attribute" };
  }
  const collectedAt = root.attributes["ai.trace.collectedAt"];
  const schemaVersion = root.attributes["ai.trace.schemaVersion"];
  if (typeof collectedAt !== "string" || typeof schemaVersion !== "string") {
    return { error: "OTel root span is missing ai.trace.collectedAt or ai.trace.schemaVersion" };
  }

  const messageSpans = spans
    .filter((s) => s.name.startsWith(SPAN_NAME_MESSAGE_PREFIX) && s.parentSpanId === root.spanId)
    .sort((a, b) => {
      const ai =
        typeof a.attributes["ai.message.index"] === "number" ? a.attributes["ai.message.index"] : 0;
      const bi =
        typeof b.attributes["ai.message.index"] === "number" ? b.attributes["ai.message.index"] : 0;
      return ai - bi;
    });

  const messages: Record<string, unknown>[] = [];
  for (const messageSpan of messageSpans) {
    const toolSpans = spans.filter(
      (s) => s.parentSpanId === messageSpan.spanId && s.name.startsWith(SPAN_NAME_TOOL_PREFIX),
    );
    const built = buildMessage(messageSpan, toolSpans);
    if (built.kind === "err") return { error: built.error };
    messages.push(built.message);
  }
  if (messages.length === 0) {
    return { error: "OTel ResourceSpans contains no message spans under the root" };
  }

  const trace: Record<string, unknown> = {
    schemaVersion,
    traceId: originalTraceId,
    sourceHarness,
    collectedAt,
    messages,
  };

  const sessionId = root.attributes["ai.session.id"];
  if (typeof sessionId === "string") {
    trace.sessionId = sessionId;
  }
  const outcome = readOutcomeFromRoot(root.attributes);
  if (outcome !== undefined) {
    trace.outcome = outcome;
  }
  const fileReferences = parseJsonAttr(root.attributes, "ai.file_references.json");
  if (Array.isArray(fileReferences)) {
    trace.fileReferences = fileReferences;
  }
  const redactions = parseJsonAttr(root.attributes, "ai.redactions.json");
  if (Array.isArray(redactions)) {
    trace.redactions = redactions;
  }
  const metadata = parseJsonAttr(root.attributes, "ai.metadata.json");
  if (isRecord(metadata)) {
    trace.metadata = metadata;
  }

  // Defensive: validate the synthesized trace against the canonical schema
  // so a broken reverse path can't silently produce invalid traces.
  const tracePayload = PublicTraceSchema.safeParse(trace);
  if (!tracePayload.success) {
    const issues = tracePayload.error.issues
      .map((i) => `${i.path.join(".")}: ${i.message}`)
      .join("; ");
    return { error: `reconstructed trace failed PublicTraceSchema validation: ${issues}` };
  }
  return { trace: tracePayload.data };
}
