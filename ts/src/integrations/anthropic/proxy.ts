/**
 * ClientProxy — Proxy-based wrapper around an Anthropic client.
 *
 * Intercepts .messages.create and .messages.stream. All other attribute
 * access passes through transparently. Mirror of Python _proxy.py for Anthropic.
 */
import { ulid } from "ulid";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { TraceSink } from "../_shared/sink.js";
import { currentSession } from "../_shared/session.js";
import { mapExceptionToReason } from "./taxonomy.js";
import {
  buildRequestSnapshot,
  buildSuccessTrace,
  buildFailureTrace,
  finalizeStreamingTrace,
  type RequestSnapshot,
} from "./trace-builder.js";
import { AnthropicStreamProxy } from "./stream-proxy.js";
import {
  hashUserId,
  hashSessionId,
  installSaltPath,
} from "../../production-traces/sdk/hashing.js";
import type { ContentBlock } from "./content.js";

export const WRAPPED_SENTINEL = Symbol.for("autocontext.wrapped");

function _nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function _resolveIdentity(
  perCall: Record<string, string> | null | undefined,
): Record<string, string> {
  let raw: Record<string, string> = {};
  if (perCall) {
    if (perCall["user_id"] != null) raw["user_id"] = perCall["user_id"];
    if (perCall["session_id"] != null) raw["session_id"] = perCall["session_id"];
  }
  if (Object.keys(raw).length === 0) {
    const ambient = currentSession();
    if (ambient.userId) raw["user_id"] = ambient.userId;
    if (ambient.sessionId) raw["session_id"] = ambient.sessionId;
  }
  if (Object.keys(raw).length === 0) return {};
  const salt = _loadSaltSync(".");
  if (!salt) return {};
  const hashed: Record<string, string> = {};
  if (raw["user_id"]) hashed["user_id_hash"] = hashUserId(raw["user_id"], salt);
  if (raw["session_id"]) hashed["session_id_hash"] = hashSessionId(raw["session_id"], salt);
  return hashed;
}

function _loadSaltSync(cwd: string): string | null {
  try {
    const saltPath = installSaltPath(cwd);
    if (!existsSync(saltPath)) return null;
    const content = readFileSync(saltPath, "utf-8").trim();
    return content || null;
  } catch {
    return null;
  }
}

let _cachedVersion: string | null = null;

function _resolvePackageVersion(): string {
  if (_cachedVersion !== null) return _cachedVersion;
  try {
    let dir = dirname(fileURLToPath(import.meta.url));
    for (let depth = 0; depth < 10; depth++) {
      const candidate = join(dir, "package.json");
      if (existsSync(candidate)) {
        const pkg = JSON.parse(readFileSync(candidate, "utf-8")) as {
          name?: string;
          version?: string;
        };
        if (pkg.name === "autoctx" && typeof pkg.version === "string") {
          _cachedVersion = pkg.version;
          return _cachedVersion;
        }
      }
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  } catch {
    // best-effort
  }
  _cachedVersion = "0.0.0";
  return _cachedVersion;
}

export class ClientProxy {
  readonly _inner: unknown;
  readonly _sink: TraceSink;
  readonly _appId: string;
  readonly _environmentTag: string;

  constructor(opts: {
    inner: unknown;
    sink: TraceSink;
    appId: string;
    environmentTag: string;
  }) {
    this._inner = opts.inner;
    this._sink = opts.sink;
    this._appId = opts.appId;
    this._environmentTag = opts.environmentTag;
  }

  _sourceInfo(): { emitter: string; sdk: { name: string; version: string } } {
    return {
      emitter: "sdk",
      sdk: { name: "autocontext-ts", version: _resolvePackageVersion() },
    };
  }

  _env(): { environmentTag: string; appId: string } {
    return { environmentTag: this._environmentTag, appId: this._appId };
  }

  async _invokeNonStreaming(kwargs: Record<string, unknown>): Promise<unknown> {
    const perCall = kwargs["autocontext"] as Record<string, string> | null;
    delete kwargs["autocontext"];
    const identity = _resolveIdentity(perCall);
    const snapshot: RequestSnapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const startedAt = _nowIso();
    const startedMonotonic = Date.now();
    let resp: unknown;
    try {
      const inner = this._inner as {
        messages: { create: (k: unknown) => Promise<unknown> };
      };
      resp = await inner.messages.create(kwargs);
    } catch (exc) {
      const endedAt = _nowIso();
      const latencyMs = Date.now() - startedMonotonic;
      const trace = buildFailureTrace({
        requestSnapshot: snapshot,
        identity,
        timing: { startedAt, endedAt, latencyMs },
        env: this._env(),
        sourceInfo: this._sourceInfo(),
        traceId: ulid(),
        reasonKey: mapExceptionToReason(exc),
        errorMessage: String(exc),
        stack: exc instanceof Error ? (exc.stack ?? null) : null,
      });
      this._sink.add(trace as unknown as Record<string, unknown>);
      throw exc;
    }
    const endedAt = _nowIso();
    const latencyMs = Date.now() - startedMonotonic;
    const r = resp as Record<string, unknown>;
    const usage = (r["usage"] as Record<string, unknown>) ?? null;
    const content = (r["content"] as Array<Record<string, unknown>>) ?? [];
    const stopReason = (r["stop_reason"] as string) ?? null;
    const trace = buildSuccessTrace({
      requestSnapshot: snapshot,
      responseContent: content as unknown as ContentBlock[],
      responseUsage: usage,
      responseStopReason: stopReason,
      identity,
      timing: { startedAt, endedAt, latencyMs },
      env: this._env(),
      sourceInfo: this._sourceInfo(),
      traceId: ulid(),
    });
    this._sink.add(trace as unknown as Record<string, unknown>);
    return resp;
  }

  _invokeStreaming(kwargs: Record<string, unknown>): AnthropicStreamProxy {
    const perCall = kwargs["autocontext"] as Record<string, string> | null;
    delete kwargs["autocontext"];
    const identity = _resolveIdentity(perCall);
    const snapshot: RequestSnapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const startedAt = _nowIso();
    const startedMonotonic = Date.now();
    const inner = this._inner as {
      messages: { create: (k: unknown) => unknown };
    };
    const rawStream = inner.messages.create(kwargs);
    const sink = this._sink;
    const env = this._env();
    const sourceInfo = this._sourceInfo();

    const onFinalize = (
      blocks: Map<number, import("./trace-builder.js").AccumulatedBlock>,
      usage: Record<string, unknown> | null,
      stopReason: string | null,
      outcome: Record<string, unknown>,
    ): void => {
      const endedAt = _nowIso();
      const latencyMs = Date.now() - startedMonotonic;
      const trace = finalizeStreamingTrace({
        requestSnapshot: snapshot,
        identity,
        timing: { startedAt, endedAt, latencyMs },
        env,
        sourceInfo,
        traceId: ulid(),
        accumulatedContentBlocks: blocks,
        accumulatedUsage: usage,
        accumulatedStopReason: stopReason,
        outcome,
      });
      sink.add(trace as unknown as Record<string, unknown>);
    };

    return new AnthropicStreamProxy({ innerStream: rawStream, onFinalize });
  }
}
