# OpenTelemetry Bridge

Optional bridge between autocontext's [`PublicTrace`](./concept-model.md)
and a minimal subset of OpenTelemetry JSON `ResourceSpans`. The bridge
lets operators move trace data between autocontext and external OTel
trace stores without losing the core agent-transcript fields.

> **Slice 1 (this doc):** TypeScript only. Python parity, OTLP protobuf
> wire format, and the `ProductionTrace` bridge are out of scope.

The TypeScript surface is exported from `autoctx`:

```ts
import {
  publicTraceToOtelResourceSpans,
  otelResourceSpansToPublicTrace,
  OtelResourceSpansSchema,
} from "autoctx";
```

## Mapping table

The bridge uses these canonical attribute names. Anything outside this
table is dropped on the reverse path or stored opaquely as a JSON blob.

### OTel span-context IDs

External OTel stores reject span-context IDs that don't match the spec
format (16-byte hex traceId, 8-byte hex spanId). The bridge emits valid
hex IDs derived deterministically by hashing the source
`PublicTrace.traceId` (and a per-span slot for span IDs):

- Forward-emit always yields valid OTel IDs.
- Running `publicTraceToOtelResourceSpans(trace)` twice on the same
  input emits byte-identical IDs (deterministic round-trip).
- The original `PublicTrace.traceId` is preserved as the `ai.trace.id`
  attribute on the root span; the reverse path uses that to reconstruct
  the PublicTrace, treating the OTel hex traceId as an opaque
  correlation handle.

### Resource attributes

| Key            | Source                      | Notes                            |
| -------------- | --------------------------- | -------------------------------- |
| `service.name` | `PublicTrace.sourceHarness` | Required on reverse-path import. |

### Root span attributes

| Key                            | Source                           | Round-trips?                          |
| ------------------------------ | -------------------------------- | ------------------------------------- |
| `ai.trace.collectedAt`         | `PublicTrace.collectedAt`        | Yes                                   |
| `ai.trace.schemaVersion`       | `PublicTrace.schemaVersion`      | Yes                                   |
| `ai.session.id`                | `PublicTrace.sessionId`          | Yes (optional)                        |
| `ai.outcome.score`             | `PublicTrace.outcome.score`      | Yes                                   |
| `ai.outcome.reasoning`         | `PublicTrace.outcome.reasoning`  | Yes                                   |
| `ai.outcome.dimensions.<name>` | `PublicTrace.outcome.dimensions` | Yes                                   |
| `ai.file_references.json`      | `PublicTrace.fileReferences`     | Lossy (JSON blob in single attribute) |
| `ai.redactions.json`           | `PublicTrace.redactions`         | Yes (JSON blob, structure preserved)  |
| `ai.metadata.json`             | `PublicTrace.metadata`           | Lossy (JSON blob in single attribute) |

The root span name is `autocontext.run:<traceId>` with kind `internal`.

### Message span attributes

One span per `PublicTrace.messages[]` entry, parented to the root span,
name `message:<role>`:

| Key                        | Source                      | Round-trips?                            |
| -------------------------- | --------------------------- | --------------------------------------- |
| `ai.role`                  | `message.role`              | Yes                                     |
| `ai.content`               | `message.content`           | Yes                                     |
| `ai.message.index`         | array index in `messages[]` | Yes (used to preserve order on reverse) |
| `ai.message.timestamp`     | `message.timestamp`         | Yes                                     |
| `ai.message.metadata.json` | `message.metadata`          | Lossy (JSON blob)                       |

### Tool-call span attributes

One span per `message.toolCalls[]` entry, parented to its message span,
name `tool:<toolName>`, kind `client`:

| Key                | Source                               | Round-trips?                                                                               |
| ------------------ | ------------------------------------ | ------------------------------------------------------------------------------------------ |
| `tool.name`        | `call.toolName`                      | Yes                                                                                        |
| `tool.index`       | array index in `message.toolCalls[]` | Yes — reverse import sorts by `tool.index` so order survives OTel sibling-span reordering. |
| `tool.args.json`   | `call.args`                          | Yes (JSON blob, parsed back into args)                                                     |
| `tool.duration_ms` | `call.durationMs`                    | Yes (optional)                                                                             |
| `tool.error`       | `call.error`                         | Yes (also surfaces as `status.code = "ERROR"`)                                             |
| `tool.result.json` | `call.result`                        | Lossy: complex result payloads can be large; OTel collectors may drop oversize attributes. |

When `tool.error` is set, the span's `status.code` is `ERROR` and the
`status.message` carries the error text.

### Boundary safety on the reverse path

`otelResourceSpansToPublicTrace()` accepts `unknown` and parses through
`OtelResourceSpansSchema` before dereferencing any field. Malformed
external JSON (such as `{ scopeSpans: [{}] }`, `null`, a string
literal) returns the documented `{ error }` result instead of throwing.
Callers can safely pass the raw output of `JSON.parse(...)` from an
external trace store.

## Round-trip guarantees

The TS test suite (`ts/tests/otel-bridge.test.ts`) pins:

- `PublicTrace -> OTel -> PublicTrace` preserves `traceId`,
  `sourceHarness`, `collectedAt`, `sessionId`, message order, message
  content, tool calls (name, args, duration, error), and outcome
  (score, reasoning, dimensions).
- Redactions metadata survives round-trip via `ai.redactions.json`.
- The reverse path validates the synthesized trace against
  `PublicTraceSchema` before returning, so a broken bridge cannot
  silently emit invalid traces.

## Known gaps (do not assume round-trip)

These fields are stored as opaque JSON inside single attributes. Third-
party OTel collectors may drop, truncate, or rename them.

- `PublicTrace.fileReferences[]` (encoded as `ai.file_references.json`)
- `PublicTrace.metadata` (encoded as `ai.metadata.json`)
- `message.metadata` (encoded per-message as `ai.message.metadata.json`)
- `ToolCall.result` (encoded as `tool.result.json`; may be very large)

If your downstream consumer relies on these fields, prefer round-
tripping the canonical autocontext `PublicTrace` JSON instead of going
through OTel.

## Privacy / retention boundary

The bridge is a pure transform: it does not call out to an OTel
collector and does not change the redaction or retention status of the
trace. Callers are responsible for:

- applying `applyRedactionPolicy(trace, policy)` _before_ converting
  to OTel if the OTel destination is less trusted than the
  `PublicTrace` source,
- carrying `PublicTrace.redactions[]` through the bridge so downstream
  consumers see that fields were redacted upstream (the test suite
  pins this).

The bridge is optional and does not replace autocontext's native trace
schema. `PublicTrace` remains the canonical form for autocontext
analytics and the cross-runtime contract (see
[concept-model.md](./concept-model.md) and the cross-runtime fixture at
`fixtures/cross-runtime/trace-finding-report.json`).

## Status (AC-682 slice 1)

- Shipped: bidirectional TS bridge + round-trip tests + this design
  note.
- Deferred: Python parity (slice 2), OTLP protobuf wire format,
  `ProductionTrace` bridge (richer shape — flat `toolCalls`, distinct
  `outcome` schema), import path into the production-traces ingest
  registry.
