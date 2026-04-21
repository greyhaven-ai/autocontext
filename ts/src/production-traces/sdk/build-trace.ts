import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { ulid } from "ulid";
import type {
  EnvContext,
  FeedbackRef,
  ProductionOutcome,
  ProductionTrace,
  ProductionTraceRouting,
  SessionIdentifier,
  TimingInfo,
  ToolCall,
  TraceMessage,
  TraceSource,
  UsageInfo,
} from "../contract/types.js";
import type { ProductionTraceId } from "../contract/branded-ids.js";
import { PRODUCTION_TRACE_SCHEMA_VERSION } from "../contract/types.js";
import { validateProductionTrace } from "./validate.js";

/**
 * Customer-facing emit-trace builder.
 *
 * DDD anchor: mirrors Python ``autocontext.production_traces.emit.build_trace``
 * verbatim (Python snake_case ↔ TS camelCase translation). Argument names,
 * default-fill behavior, and validation semantics match Python exactly so
 * customers using both SDKs share one mental model (enforced by the cross-
 * runtime property test P-cross-runtime-emit-parity at 50 runs).
 *
 * DRY anchor: this module neither re-defines any contract types nor
 * duplicates the validator. It composes :func:`validateProductionTrace` from
 * ``sdk/validate.ts``, which in turn wraps the AJV validator in
 * ``contract/validators.ts``. The JSON Schemas remain the single source of
 * truth.
 */

/**
 * Build a ``ProductionTrace`` from a customer-facing input shape. Defaults
 * are filled, the assembled document is validated via AJV, and on failure a
 * :class:`ValidationError` is raised with the per-field error list.
 *
 * Returns a plain object (not frozen) so customers may still enrich the
 * trace — for example attaching ``metadata.rawProviderPayload`` — before
 * handing it to :func:`writeJsonl`.
 */
export interface BuildTraceInputs {
  /** Provider name. Must be one of the enum values accepted by the schema. */
  readonly provider: string;
  /** Model identifier sent to the provider. Must be non-empty. */
  readonly model: string;
  /** Chronological list of messages in this trace; schema requires minItems: 1. */
  readonly messages: readonly TraceMessage[];
  /** Timing envelope — startedAt, endedAt, latencyMs, optional TTFT. */
  readonly timing: TimingInfo;
  /** Usage envelope — tokensIn, tokensOut, optional cost, optional raw. */
  readonly usage: UsageInfo;
  /** Environment context — environmentTag, appId, optional taskType, deploymentMeta. */
  readonly env: EnvContext;
  /** Optional ULID; defaults to a freshly-generated one. */
  readonly traceId?: ProductionTraceId | string;
  /** Optional session identifier (user/session hash, request id). */
  readonly session?: SessionIdentifier;
  /** Optional graded outcome (success/failure/partial/unknown + score + signals). */
  readonly outcome?: ProductionOutcome;
  /** Optional tool-call list; defaults to ``[]``. */
  readonly toolCalls?: readonly ToolCall[];
  /** Optional feedback references; defaults to ``[]``. */
  readonly feedbackRefs?: readonly FeedbackRef[];
  /** Optional routing decision (AC-545 field). */
  readonly routing?: ProductionTraceRouting;
  /** Optional free-form metadata object. */
  readonly metadata?: Readonly<Record<string, unknown>>;
  /** Optional source; defaults to the SDK's emitter identity. */
  readonly source?: TraceSource;
  /**
   * Optional ISO-8601 collected-at timestamp. Accepted for forward-compat with
   * spec §4.1; the current schema does NOT include a ``collectedAt`` field, so
   * the value is discarded from the output to remain byte-identical with
   * Python's ``build_trace`` (which also ignores the concept).
   */
  readonly collectedAt?: string;
}

/**
 * Assemble a ProductionTrace dict, fill defaults, validate, and return it.
 *
 * Raises :class:`ValidationError` on schema violations with per-field detail
 * accessible via ``err.fieldErrors``. The returned object is not frozen —
 * customer code may mutate / merge freely (matches Python's ``dict`` return).
 */
export function buildTrace(inputs: BuildTraceInputs): ProductionTrace {
  const traceId = (inputs.traceId ?? ulid()) as ProductionTraceId;
  const source = inputs.source ?? defaultSource();

  // Assemble as a plain Record so we can conditionally include optionals
  // without the structural-typing friction of the ProductionTrace shape.
  // AJV is the final arbiter — we validate the assembled object before return.
  const trace: Record<string, unknown> = {
    schemaVersion: PRODUCTION_TRACE_SCHEMA_VERSION,
    traceId,
    source,
    provider: { name: inputs.provider },
    model: inputs.model,
    env: inputs.env,
    messages: inputs.messages,
    toolCalls: inputs.toolCalls ?? [],
    timing: inputs.timing,
    usage: inputs.usage,
    feedbackRefs: inputs.feedbackRefs ?? [],
    links: {},
    redactions: [],
  };
  if (inputs.session !== undefined) trace.session = inputs.session;
  if (inputs.outcome !== undefined) trace.outcome = inputs.outcome;
  if (inputs.routing !== undefined) trace.routing = inputs.routing;
  if (inputs.metadata !== undefined) trace.metadata = inputs.metadata;
  // ``collectedAt`` is intentionally NOT copied into the output — Python
  // parity requires byte-identity and Python's build_trace does not emit it.

  return validateProductionTrace(trace);
}

// ---- internals ----

/**
 * The SDK's self-describing emitter identity. Mirrors Python's
 * ``_default_source`` (``emitter: "sdk"``) and names the SDK
 * ``"autocontext-ts"`` to let operator-side ingestion distinguish between
 * Python vs TS customer callers when analyzing traces.
 */
function defaultSource(): TraceSource {
  return {
    emitter: "sdk",
    sdk: {
      name: "autocontext-ts",
      version: sdkVersion(),
    },
  };
}

// Resolved once at module load. The autoctx package version is baked into
// ``package.json``; in test / dev we fall back to "0.0.0" to match Python's
// behavior when ``importlib.metadata.version`` fails on an editable install.
let cachedVersion: string | null = null;

function sdkVersion(): string {
  if (cachedVersion !== null) return cachedVersion;
  cachedVersion = resolveVersionFromPackageJson();
  return cachedVersion;
}

/**
 * Resolve the running autoctx package version. Walks up from this module
 * looking for the first ``package.json`` whose ``name`` is ``autoctx``.
 * Pure synchronous resolution — no dynamic imports and no network.
 */
function resolveVersionFromPackageJson(): string {
  try {
    let dir = dirname(fileURLToPath(import.meta.url));
    for (let depth = 0; depth < 10; depth++) {
      const candidate = join(dir, "package.json");
      if (existsSync(candidate)) {
        const pkg = JSON.parse(readFileSync(candidate, "utf-8")) as { name?: string; version?: string };
        if (pkg.name === "autoctx" && typeof pkg.version === "string") {
          return pkg.version;
        }
      }
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  } catch {
    // Pure best-effort — fall through to the safe default.
  }
  return "0.0.0";
}
