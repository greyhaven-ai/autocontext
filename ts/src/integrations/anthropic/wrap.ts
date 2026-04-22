/**
 * instrumentClient factory for Anthropic SDK clients.
 *
 * Wraps an Anthropic client with a Proxy that intercepts .messages.create
 * and .messages.stream calls. Double-wrap detection + identity resolution.
 * Mirror of Python _wrap.py for Anthropic.
 */
import type { TraceSink } from "../_shared/sink.js";
import { ClientProxy, WRAPPED_SENTINEL } from "./proxy.js";

export function instrumentClient<T>(
  client: T,
  opts: {
    sink: TraceSink;
    appId?: string;
    environmentTag?: string;
  },
): T {
  // Double-wrap guard
  if ((client as Record<symbol, boolean>)[WRAPPED_SENTINEL]) {
    throw new Error("client is already wrapped");
  }
  // Resolve app_id
  const resolvedAppId = opts.appId ?? process.env["AUTOCONTEXT_APP_ID"];
  if (!resolvedAppId) {
    throw new Error(
      "app_id is required — pass appId: ... to instrumentClient() or set AUTOCONTEXT_APP_ID env var",
    );
  }
  const proxy = new ClientProxy({
    inner: client,
    sink: opts.sink,
    appId: resolvedAppId,
    environmentTag: opts.environmentTag ?? "production",
  });

  return new Proxy(client as object, {
    get(target, prop) {
      if (prop === WRAPPED_SENTINEL) return true;
      if (prop === "messages") {
        return {
          create: (kwargs: Record<string, unknown>) => {
            if (kwargs["stream"]) {
              return proxy._invokeStreaming({ ...kwargs });
            }
            return proxy._invokeNonStreaming({ ...kwargs });
          },
          stream: (kwargs: Record<string, unknown>) => {
            return proxy._invokeStreaming({ ...kwargs, stream: true });
          },
        };
      }
      return (target as Record<string | symbol, unknown>)[prop];
    },
  }) as T;
}
