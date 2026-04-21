/**
 * instrumentClient factory — double-wrap detection + identity resolution.
 *
 * Spec §4.1. Mirror of Python ``_wrap.py``.
 */
import type { TraceSink } from "./sink.js";
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
      if (prop === "chat") {
        return new Proxy(
          (target as Record<string | symbol, unknown>)["chat"] as object,
          {
            get(_chatTarget, chatProp) {
              if (chatProp === "completions") {
                return {
                  create: (kwargs: Record<string, unknown>) =>
                    proxy._invokeChatCompletionsCreate({ ...kwargs }),
                };
              }
              return (_chatTarget as Record<string | symbol, unknown>)[chatProp];
            },
          },
        );
      }
      if (prop === "responses") {
        return {
          create: (kwargs: Record<string, unknown>) => {
            const normalizedMessages = (kwargs["messages"] as Array<Record<string, unknown>>) ??
              [{ role: "user", content: kwargs["input"] ?? "" }];
            const kwargsForCreate = { ...kwargs };
            delete kwargsForCreate["input"];
            return proxy._invokeResponsesCreate(kwargsForCreate, normalizedMessages);
          },
        };
      }
      return (target as Record<string | symbol, unknown>)[prop];
    },
  }) as T;
}
