/**
 * AC-682 (slice 1): bidirectional bridge between `PublicTrace` and a
 * minimal subset of OpenTelemetry JSON `ResourceSpans`.
 *
 * Scope: TypeScript only; the bridge is optional and does not replace
 * AutoContext's native trace schema. Python parity, OTLP protobuf wire
 * format, and the ProductionTrace bridge are out of scope.
 *
 * See `docs/opentelemetry-bridge.md` for the canonical attribute
 * vocabulary and the known-gap list (fields that don't round-trip
 * cleanly).
 */

import { z } from "zod";

import {
  PublicTraceSchema,
  type PublicTrace,
  type TraceMessage,
  type ToolCall,
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
  traceId: z.string().min(1),
  spanId: z.string().min(1),
  parentSpanId: z.string().min(1).optional(),
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

function spanId(prefix: string, index: number): string {
  // Stable, deterministic span ids derived from a prefix + index. Real OTel
  // producers use 16-hex-char ids; ours are deterministic strings so the
  // forward/reverse round-trip is byte-identical without a counter.
  return `${prefix}-${index.toString().padStart(2, "0")}`;
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

function attributesForToolCall(call: ToolCall): OtelAttributes {
  const attrs: OtelAttributes = {
    "tool.name": call.toolName,
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
  const spans: OtelSpan[] = [];
  const rootStart = isoToUnixNano(trace.collectedAt);

  const rootId = spanId("root", 0);
  spans.push({
    traceId: trace.traceId,
    spanId: rootId,
    name: `autocontext.run:${trace.traceId}`,
    kind: "internal",
    startTimeUnixNano: rootStart,
    attributes: rootSpanAttributes(trace),
  });

  trace.messages.forEach((message, index) => {
    const messageSpanId = spanId("msg", index);
    spans.push({
      traceId: trace.traceId,
      spanId: messageSpanId,
      parentSpanId: rootId,
      name: `${SPAN_NAME_MESSAGE_PREFIX}${message.role}`,
      kind: "internal",
      startTimeUnixNano: isoToUnixNano(message.timestamp),
      attributes: attributesForMessage(message, index),
    });

    (message.toolCalls ?? []).forEach((call, callIndex) => {
      const toolSpanIdValue = spanId(`tool-${index}`, callIndex);
      const span: OtelSpan = {
        traceId: trace.traceId,
        spanId: toolSpanIdValue,
        parentSpanId: messageSpanId,
        name: `${SPAN_NAME_TOOL_PREFIX}${call.toolName}`,
        kind: "client",
        startTimeUnixNano: isoToUnixNano(message.timestamp),
        attributes: attributesForToolCall(call),
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
// Reverse: OtelResourceSpans -> PublicTrace
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

function messageFromSpan(
  messageSpan: OtelSpan,
  toolSpans: readonly OtelSpan[],
): TraceMessage | { error: string } {
  const role = messageSpan.attributes["ai.role"];
  const content = messageSpan.attributes["ai.content"];
  const timestamp = messageSpan.attributes["ai.message.timestamp"];
  if (typeof role !== "string" || typeof content !== "string" || typeof timestamp !== "string") {
    return {
      error: `message span ${messageSpan.spanId} missing ai.role/ai.content/ai.message.timestamp`,
    };
  }
  if (!["user", "assistant", "system", "tool"].includes(role)) {
    return { error: `message span ${messageSpan.spanId} has unknown role ${role}` };
  }
  const message: TraceMessage = {
    role: role as TraceMessage["role"],
    content,
    timestamp,
  };
  const metadata = parseJsonAttr(messageSpan.attributes, "ai.message.metadata.json");
  if (metadata !== undefined && typeof metadata === "object" && metadata !== null) {
    (message as { metadata?: Record<string, unknown> }).metadata = metadata as Record<
      string,
      unknown
    >;
  }
  const calls: ToolCall[] = [];
  for (const toolSpan of toolSpans) {
    const toolName = toolSpan.attributes["tool.name"];
    if (typeof toolName !== "string") continue;
    const argsRaw = parseJsonAttr(toolSpan.attributes, "tool.args.json");
    const args =
      argsRaw !== null && typeof argsRaw === "object" ? (argsRaw as Record<string, unknown>) : {};
    const call: ToolCall = { toolName, args };
    const duration = toolSpan.attributes["tool.duration_ms"];
    if (typeof duration === "number") (call as { durationMs?: number }).durationMs = duration;
    const error = toolSpan.attributes["tool.error"];
    if (typeof error === "string") (call as { error?: string }).error = error;
    const result = parseJsonAttr(toolSpan.attributes, "tool.result.json");
    if (result !== undefined) (call as { result?: unknown }).result = result;
    calls.push(call);
  }
  if (calls.length > 0) {
    (message as { toolCalls?: ToolCall[] }).toolCalls = calls;
  }
  return message;
}

export function otelResourceSpansToPublicTrace(input: OtelResourceSpans): OtelToPublicTraceResult {
  const sourceHarness = input.resource.attributes["service.name"];
  if (typeof sourceHarness !== "string" || sourceHarness.length === 0) {
    return { error: "OTel ResourceSpans is missing resource.attributes['service.name']" };
  }
  const spans = flatSpans(input);
  const root = spans.find((s) => s.parentSpanId === undefined);
  if (root === undefined) {
    return { error: "OTel ResourceSpans has no root span (every span has parentSpanId set)" };
  }

  const collectedAt = root.attributes["ai.trace.collectedAt"];
  const schemaVersion = root.attributes["ai.trace.schemaVersion"];
  if (typeof collectedAt !== "string" || typeof schemaVersion !== "string") {
    return { error: "OTel root span is missing ai.trace.collectedAt or ai.trace.schemaVersion" };
  }

  const messageSpans = spans
    .filter((s) => s.name.startsWith(SPAN_NAME_MESSAGE_PREFIX) && s.parentSpanId === root.spanId)
    .sort((a, b) => {
      const ai = (a.attributes["ai.message.index"] ?? 0) as number;
      const bi = (b.attributes["ai.message.index"] ?? 0) as number;
      return ai - bi;
    });

  const messages: TraceMessage[] = [];
  for (const messageSpan of messageSpans) {
    const toolSpans = spans.filter(
      (s) => s.parentSpanId === messageSpan.spanId && s.name.startsWith(SPAN_NAME_TOOL_PREFIX),
    );
    const built = messageFromSpan(messageSpan, toolSpans);
    if ("error" in built) return { error: built.error };
    messages.push(built);
  }
  if (messages.length === 0) {
    return { error: "OTel ResourceSpans contains no message spans under the root" };
  }

  const trace: PublicTrace = {
    schemaVersion: schemaVersion as PublicTrace["schemaVersion"],
    traceId: root.traceId,
    sourceHarness,
    collectedAt,
    messages,
  };

  const sessionId = root.attributes["ai.session.id"];
  if (typeof sessionId === "string") {
    (trace as { sessionId?: string }).sessionId = sessionId;
  }
  const outcome = readOutcomeFromRoot(root.attributes);
  if (outcome !== undefined) {
    (trace as { outcome?: PublicTrace["outcome"] }).outcome = outcome;
  }
  const fileReferences = parseJsonAttr(root.attributes, "ai.file_references.json");
  if (Array.isArray(fileReferences)) {
    (trace as { fileReferences?: PublicTrace["fileReferences"] }).fileReferences =
      fileReferences as PublicTrace["fileReferences"];
  }
  const redactions = parseJsonAttr(root.attributes, "ai.redactions.json");
  if (Array.isArray(redactions)) {
    (trace as { redactions?: PublicTrace["redactions"] }).redactions =
      redactions as PublicTrace["redactions"];
  }
  const metadata = parseJsonAttr(root.attributes, "ai.metadata.json");
  if (metadata !== null && typeof metadata === "object") {
    (trace as { metadata?: Record<string, unknown> }).metadata = metadata as Record<
      string,
      unknown
    >;
  }

  // Defensive: validate the synthesized trace against the canonical schema
  // so a broken reverse path can't silently produce invalid traces.
  const parsed = PublicTraceSchema.safeParse(trace);
  if (!parsed.success) {
    const issues = parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ");
    return { error: `reconstructed trace failed PublicTraceSchema validation: ${issues}` };
  }
  return { trace: parsed.data };
}
